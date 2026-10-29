package codingagents

import (
	"context"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"strings"
	"sync"
	"time"

	"github.com/aristoteleo/pantheonos/os/childenv"
)

func workspaceEnv() []string { return childenv.Workspace() }

// Status is what the desktop shows next to a detected agent.
type Status struct {
	Version string `json:"version,omitempty"`
	// SignedIn is nil when PantheonOS cannot tell; it never reads a
	// credential, only whether one is there.
	SignedIn *bool `json:"signed_in,omitempty"`
}

type versionKey struct {
	path string
	mod  time.Time
}

var (
	versionMu sync.Mutex
	versions  = map[versionKey]string{}
)

// Inspect reports an agent's version (cached until its binary changes) and
// whether it has a sign-in.
func Inspect(f Found) Status {
	return Status{Version: version(f.Path), SignedIn: signedIn(f.ID, f.Path)}
}

func version(path string) string {
	st, err := os.Stat(path)
	if err != nil {
		return ""
	}
	key := versionKey{path, st.ModTime()}
	versionMu.Lock()
	v, ok := versions[key]
	versionMu.Unlock()
	if ok {
		return v
	}
	ctx, cancel := context.WithTimeout(context.Background(), 8*time.Second)
	defer cancel()
	cmd := exec.CommandContext(ctx, path, "--version")
	cmd.Env = AgentEnv("")
	out, _ := cmd.Output()
	v = strings.TrimSpace(strings.SplitN(string(out), "\n", 2)[0])
	if len(v) > 60 {
		v = v[:60]
	}
	versionMu.Lock()
	versions[key] = v
	versionMu.Unlock()
	return v
}

func yes(b bool) *bool { return &b }

func exists(paths ...string) bool {
	for _, p := range paths {
		if _, err := os.Stat(p); err == nil {
			return true
		}
	}
	return false
}

func anyEnv(names ...string) bool {
	for _, n := range names {
		if os.Getenv(n) != "" {
			return true
		}
	}
	return false
}

func signedIn(id, path string) *bool {
	home, _ := os.UserHomeDir()
	switch id {
	case PantheonAgent:
		// Signed in with the node's key, once the user lets agents here use PantheonOS.
		return yes(LoadSettings().Access.Key != "")
	case "claude":
		dir := os.Getenv("CLAUDE_CONFIG_DIR")
		if dir == "" {
			dir = filepath.Join(home, ".claude")
		}
		if anyEnv("ANTHROPIC_API_KEY", "CLAUDE_CODE_OAUTH_TOKEN") || exists(filepath.Join(dir, ".credentials.json")) {
			return yes(true)
		}
		if runtime.GOOS == "darwin" {
			// The login lives in the Keychain; this asks whether the item
			// exists, without reading it.
			err := exec.Command("/usr/bin/security", "find-generic-password", "-s", "Claude Code-credentials").Run()
			return yes(err == nil)
		}
		return yes(false)
	case "codex":
		dir := os.Getenv("CODEX_HOME")
		if dir == "" {
			dir = filepath.Join(home, ".codex")
		}
		return yes(anyEnv("OPENAI_API_KEY", "CODEX_API_KEY") || exists(filepath.Join(dir, "auth.json")))
	case "gemini":
		return yes(anyEnv("GEMINI_API_KEY", "GOOGLE_API_KEY") || exists(filepath.Join(home, ".gemini", "oauth_creds.json")))
	case "cursor":
		if anyEnv("CURSOR_API_KEY") {
			return yes(true)
		}
		ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
		defer cancel()
		cmd := exec.CommandContext(ctx, path, "status")
		cmd.Env = AgentEnv("")
		out, err := cmd.CombinedOutput()
		if err != nil && ctx.Err() != nil {
			return nil
		}
		text := strings.ToLower(string(out))
		if strings.Contains(text, "not logged in") {
			return yes(false)
		}
		if strings.Contains(text, "logged in") {
			return yes(true)
		}
	}
	return nil
}

// parentSessionVars mark a process as running inside another agent's
// session. A node started from such a terminal passes them on, and an agent
// that inherits them treats itself as a child of that session (Claude Code
// stops saving its transcript). User configuration is kept.
var parentSessionVars = []string{"CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT", "CLAUDE_CODE_CHILD_SESSION",
	"CLAUDE_CODE_SESSION_ID", "CLAUDE_CODE_SESSION_ATTENDED", "CLAUDE_CODE_BRIDGE_SESSION_ID",
	"CLAUDE_CODE_MESSAGING_SOCKET", "CLAUDE_CODE_MESSAGING_TOKEN", "CLAUDE_CODE_EXECPATH", "CLAUDE_PID",
	"CODEX_THREAD_ID", "CODEX_SANDBOX", "CODEX_SANDBOX_NETWORK_DISABLED"}

// CleanEnv drops another agent session's markers from env.
func CleanEnv(env []string) []string {
	out := env[:0:0]
	for _, kv := range env {
		name, _, _ := strings.Cut(kv, "=")
		drop := false
		for _, v := range parentSessionVars {
			if name == v {
				drop = true
				break
			}
		}
		if !drop {
			out = append(out, kv)
		}
	}
	return out
}

// AgentEnv is the environment an agent runs with: the workspace environment
// without another session's markers, PATH as the login shell sets it (an
// npm-installed agent starts with `#!/usr/bin/env node`), and what agentID
// itself needs. Claude Code refuses full-auto as root unless told it is in a
// sandbox; a node running as root is a disposable workspace container.
func AgentEnv(agentID string) []string {
	env := CleanEnv(workspaceEnv())
	path := strings.Join(cliPath(SearchDirs(false)), string(os.PathListSeparator))
	out := make([]string, 0, len(env)+2)
	for _, kv := range env {
		if !strings.HasPrefix(kv, "PATH=") {
			out = append(out, kv)
		}
	}
	out = append(out, "PATH="+path)
	return append(out, ExtraEnv(agentID)...)
}

// ExtraEnv is what one agent needs beyond the user's environment.
func ExtraEnv(agentID string) []string {
	if agentID == "claude" && os.Geteuid() == 0 {
		return []string{"IS_SANDBOX=1"}
	}
	return nil
}
