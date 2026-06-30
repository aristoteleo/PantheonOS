// Package exec runs a Task on the local machine by spawning a subprocess.
// (Phase 2 will add streamed output and a managed Python toolset endpoint.)
package exec

import (
	"bytes"
	"context"
	"os"
	"os/exec"
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
	return res
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
