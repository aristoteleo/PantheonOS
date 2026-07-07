package exec

import (
	"strings"
	"testing"
)

// A "windows" Node must run shell code in PowerShell via -Command (native, no
// WSL/bash), and everything else via a POSIX shell's -c. run_on_node's contract
// with the agent depends on this split.
func TestShellInvocationFor(t *testing.T) {
	code := "echo hi"

	winBin, winArgs := shellInvocationFor("windows", code)
	if !(strings.Contains(winBin, "powershell") || strings.Contains(winBin, "pwsh")) {
		t.Fatalf("windows shell = %q, want powershell/pwsh", winBin)
	}
	if got := strings.Join(winArgs, " "); got != "-NoProfile -NonInteractive -Command "+code {
		t.Fatalf("windows args = %q, want PowerShell -Command form", got)
	}

	for _, goos := range []string{"linux", "darwin"} {
		_, args := shellInvocationFor(goos, code)
		if len(args) != 2 || args[0] != "-c" || args[1] != code {
			t.Fatalf("%s args = %v, want [-c %q]", goos, args, code)
		}
	}
}
