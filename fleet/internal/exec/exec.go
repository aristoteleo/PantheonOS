// Package exec runs a Task on the local machine by spawning a subprocess.
// (Phase 2 will add streamed output and a managed Python toolset endpoint.)
package exec

import (
	"bytes"
	"context"
	"os"
	"os/exec"
	"path/filepath"
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

	// On macOS, "Operation not permitted" almost always means the OS blocked a
	// TCC-protected path (Downloads/Documents/Desktop). Trip the NATIVE folder
	// prompt with fleet's OWN identity, then hint the agent to relay a one-click
	// Allow + retry (see nudgeMacPermission).
	if isMacPermissionDenied(res.Stdout + res.Stderr) {
		nudgeMacPermission(res.Stdout + res.Stderr)
		res.Stderr += "\n[pantheon-fleet] macOS privacy (TCC) blocked this folder. A native permission prompt (“fleet” wants to access that folder) should now be on the Mac — click Allow, then retry. No Full Disk Access setup needed."
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

// nudgeMacPermission trips the NATIVE macOS folder-access prompt for the folder a
// task was denied. The fleet binary ships NS{Downloads,Documents,Desktop}Folder-
// UsageDescription keys and is Developer-ID signed, so when IT (not a subprocess)
// reads a protected folder, tccd shows a “‘fleet’ wants to access your Downloads
// folder” prompt — one Allow, no Full Disk Access spelunking. Grants attach to the
// signed binary, so the next task's subprocess (fleet is its responsible process)
// then succeeds. Rate-limited + fire-and-forget; the os.Open blocks on the prompt,
// so it runs off the task path.
func nudgeMacPermission(output string) {
	if runtime.GOOS != "darwin" {
		return
	}
	permMu.Lock()
	if !lastPermReq.IsZero() && time.Since(lastPermReq) < 30*time.Second {
		permMu.Unlock()
		return
	}
	lastPermReq = time.Now()
	permMu.Unlock()

	go func() {
		for _, dir := range protectedDirsInvolved(output) {
			if f, err := os.Open(dir); err == nil {
				_, _ = f.Readdirnames(1) // the read is what trips TCC → native prompt
				_ = f.Close()
			}
		}
	}()
}

// protectedDirsInvolved returns the TCC-protected folders named in the denial
// output (so we prompt for exactly what was blocked), or the common trio when the
// path isn't recognisable.
func protectedDirsInvolved(output string) []string {
	home, err := os.UserHomeDir()
	if err != nil {
		return nil
	}
	common := []string{
		filepath.Join(home, "Downloads"),
		filepath.Join(home, "Documents"),
		filepath.Join(home, "Desktop"),
	}
	var hit []string
	for _, d := range common {
		if strings.Contains(output, d) {
			hit = append(hit, d)
		}
	}
	if len(hit) > 0 {
		return hit
	}
	return common
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
