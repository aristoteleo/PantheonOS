//go:build windows

package exec

import (
	"context"
	"strings"
	"testing"

	"github.com/aristoteleo/pantheon-fleet/internal/proto"
)

// These exercise the REAL Run() path on a real Windows machine (CI:
// windows-latest). The bug they guard: shell Tasks used to run via `bash -c`,
// which on a box with WSL resolves to WSL's bash — so commands executed inside
// the Linux subsystem instead of natively. A shell Task must now run in native
// PowerShell: PowerShell syntax works, paths are Windows paths, no /mnt/c.

func TestRunShellIsNativePowerShell(t *testing.T) {
	ctx := context.Background()

	// $PSVersionTable is a PowerShell automatic variable — sh/bash can't produce it.
	if res := Run(ctx, proto.Task{TaskID: "ps-ver", Kind: proto.TaskShell,
		Code: "$PSVersionTable.PSVersion.Major"}); res.ExitCode != 0 || res.Error != "" ||
		strings.TrimSpace(res.Stdout) == "" {
		t.Fatalf("PowerShell probe failed: exit=%d err=%q stdout=%q stderr=%q",
			res.ExitCode, res.Error, res.Stdout, res.Stderr)
	}

	// $env: is PowerShell env syntax; bash would just print an empty line.
	if res := Run(ctx, proto.Task{TaskID: "ps-env", Kind: proto.TaskShell,
		Code: "Write-Output $env:COMPUTERNAME"}); strings.TrimSpace(res.Stdout) == "" {
		t.Fatalf("Write-Output $env:COMPUTERNAME empty — interpreter isn't PowerShell (stderr=%q)", res.Stderr)
	}

	// The working directory must be a native Windows path, NOT a WSL mount.
	res := Run(ctx, proto.Task{TaskID: "ps-cwd", Kind: proto.TaskShell, Code: "(Get-Location).Path"})
	out := strings.TrimSpace(res.Stdout)
	if !strings.Contains(out, `:\`) {
		t.Fatalf("(Get-Location).Path = %q, want a native C:\\-style path", out)
	}
	if strings.Contains(out, "/mnt/") || strings.HasPrefix(out, "/home/") {
		t.Fatalf("(Get-Location).Path = %q looks like WSL, not native Windows", out)
	}
}

// Python Tasks use pythonBin() (python/py on Windows) — verify it resolves and
// reports the native OS. Skips (not fails) if this host has no Python.
func TestRunPythonOnWindows(t *testing.T) {
	res := Run(context.Background(), proto.Task{TaskID: "py", Kind: proto.TaskPython,
		Code: "import platform; print(platform.system())"})
	if res.ExitCode != 0 || res.Error != "" {
		t.Skipf("python not runnable on this host (exit=%d err=%q stderr=%q)", res.ExitCode, res.Error, res.Stderr)
	}
	if got := strings.TrimSpace(res.Stdout); got != "Windows" {
		t.Fatalf("platform.system() = %q, want Windows", got)
	}
}
