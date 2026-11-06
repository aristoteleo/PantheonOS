//go:build !darwin && !linux

package codingagents

// sandboxKind: no sandbox on this system (Windows and others), so shared
// agents' turns are refused unless the owner turned the sandbox off.
func sandboxKind() (kind, why string) { return "", "no sandbox on this system" }

// Wrap refuses: there is no sandbox here.
func (s Sandbox) Wrap(args []string) (string, []string, error) { return "", nil, ErrNoSandbox }
