// Package exec runs a Task on the local machine by spawning a subprocess.
// (Phase 2 will add streamed output and a managed Python toolset endpoint.)
package exec

import (
	"bytes"
	"context"
	"os"
	"os/exec"
	"runtime"
	"strings"
	"sync"
	"time"

	"github.com/aristoteleo/pantheon-fleet/internal/proto"
)

// Run executes a Task and returns its result. shell -> bash -c; python -> python3 -c.
func Run(ctx context.Context, t proto.Task) proto.TaskResult {
	res := proto.TaskResult{TaskID: t.TaskID}

	timeout := time.Duration(t.TimeoutS) * time.Second
	if timeout <= 0 {
		timeout = time.Hour
	}
	cctx, cancel := context.WithTimeout(ctx, timeout)
	defer cancel()

	var cmd *exec.Cmd
	switch t.Kind {
	case proto.TaskPython:
		cmd = exec.CommandContext(cctx, pythonBin(), "-c", t.Code)
	default: // shell
		cmd = exec.CommandContext(cctx, shellBin(), "-c", t.Code)
	}
	if t.Cwd != "" {
		cmd.Dir = t.Cwd
	}
	cmd.Env = append(os.Environ(), envSlice(t.Env)...)

	var stdout, stderr bytes.Buffer
	cmd.Stdout = &stdout
	cmd.Stderr = &stderr

	err := cmd.Run()
	res.Stdout = stdout.String()
	res.Stderr = stderr.String()

	switch {
	case cctx.Err() == context.DeadlineExceeded:
		res.Error = "timeout"
		res.ExitCode = -1
	case err != nil:
		if ee, ok := err.(*exec.ExitError); ok {
			res.ExitCode = ee.ExitCode()
		} else {
			res.Error = err.Error()
			res.ExitCode = -1
		}
	}

	// On macOS, a "Operation not permitted" almost always means the OS blocked a
	// TCC-protected path (Downloads/Documents/Desktop/Full Disk). Ask the user for
	// access on-demand — pop a dialog on the node's own login session and offer to
	// open the Full Disk Access pane — and give the agent a clear hint to relay.
	if isMacPermissionDenied(res.Stdout + res.Stderr) {
		requestMacPermission()
		res.Stderr += "\n[pantheon-fleet] macOS blocked this file access (privacy/TCC). A permission request was shown on the Mac — grant Full Disk Access to the terminal running fleet, then retry."
	}
	return res
}

var (
	permMu      sync.Mutex
	lastPermReq time.Time
)

// isMacPermissionDenied reports whether output looks like a macOS TCC denial.
func isMacPermissionDenied(output string) bool {
	return runtime.GOOS == "darwin" && strings.Contains(output, "Operation not permitted")
}

// requestMacPermission shows an on-demand permission dialog in the node's GUI
// session (fleet runs from the user's terminal) and, if they choose, opens the
// Full Disk Access settings pane. Rate-limited (once/min) and fire-and-forget so
// it never blocks or spams task execution.
func requestMacPermission() {
	permMu.Lock()
	if !lastPermReq.IsZero() && time.Since(lastPermReq) < time.Minute {
		permMu.Unlock()
		return
	}
	lastPermReq = time.Now()
	permMu.Unlock()

	go func() {
		const script = `display dialog "Pantheon Fleet needs permission to access files on this Mac. Grant Full Disk Access to your terminal (Terminal / iTerm), then retry the request." with title "Pantheon Fleet" buttons {"Later", "Open Settings"} default button "Open Settings"`
		out, err := exec.Command("osascript", "-e", script).Output()
		if err != nil {
			return // no GUI session, or the user dismissed it
		}
		if strings.Contains(string(out), "Open Settings") {
			_ = exec.Command("open", "x-apple.systempreferences:com.apple.preference.security?Privacy_AllFiles").Run()
		}
	}()
}

func envSlice(m map[string]string) []string {
	out := make([]string, 0, len(m))
	for k, v := range m {
		out = append(out, k+"="+v)
	}
	return out
}

func shellBin() string {
	if _, err := exec.LookPath("bash"); err == nil {
		return "bash"
	}
	return "sh"
}

func pythonBin() string {
	if _, err := exec.LookPath("python3"); err == nil {
		return "python3"
	}
	return "python"
}
