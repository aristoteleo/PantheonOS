package codingagentsapp

import (
	"context"
	"os"
	"os/exec"
	"strings"
	"sync"
	"time"
)

// teamCleanArgs start a Team Chat turn's runtime without the computer
// user's own setup and without the runtime setup in the agent's folder,
// where the runtime can (docs/apps/team-chat.md, "What Team Chat cannot
// prevent"): people who may use a shared agent should not reach the user's
// plugins, MCP servers, connectors and hooks through it, and a hook or
// setting the agent once wrote into its own folder (for one person) must
// not run in later turns (for others).
//   - Codex: --ignore-user-config (no $CODEX_HOME/config.toml: its plugins,
//     MCP servers, notify hooks; and, as the project trust lives there, no
//     .codex/config.toml of the folder; its sign-in still works), and
//     --disable hooks (no hooks from anywhere) when it has hooks.
//   - Claude Code: no settings files at all (--setting-sources "": not the
//     user's, not the folder's .claude/settings.json or
//     settings.local.json, so none of their hooks, plugins, permissions,
//     env, helpers; nor the folder's CLAUDE.md or .claude/ skills), and no
//     MCP servers.
//
// Older versions without the flags, and other runtimes, start as before.
// Coding Agents' own turns are unchanged.
func teamCleanArgs(agent, path string) []string {
	switch agent {
	case "codex":
		help := runtimeHelp(path, "exec", "--help")
		if !strings.Contains(help, "--ignore-user-config") {
			return nil
		}
		out := []string{"--ignore-user-config"}
		if strings.Contains(help, "--disable") && hasFeature(runtimeHelp(path, "features", "list"), "hooks") {
			out = append(out, "--disable", "hooks")
		}
		return out
	case "claude":
		help := runtimeHelp(path, "--help")
		if strings.Contains(help, "--setting-sources") && strings.Contains(help, "--strict-mcp-config") {
			return []string{"--setting-sources", "", "--strict-mcp-config"}
		}
	}
	return nil
}

// hasFeature: `codex features list` names feature (its first column).
func hasFeature(list, feature string) bool {
	for _, line := range strings.Split(list, "\n") {
		if f := strings.Fields(line); len(f) > 0 && f[0] == feature {
			return true
		}
	}
	return false
}

var (
	helpMu    sync.Mutex
	helpCache = map[string]string{}
)

// runtimeHelp is a runtime's help text for args (cached per program and its
// modification time); a variable so tests give their own.
var runtimeHelp = func(path string, args ...string) string {
	key := path + "\x00" + strings.Join(args, " ")
	if fi, err := os.Stat(path); err == nil {
		key += "\x00" + fi.ModTime().String()
	}
	helpMu.Lock()
	defer helpMu.Unlock()
	if h, ok := helpCache[key]; ok {
		return h
	}
	ctx, cancel := context.WithTimeout(context.Background(), 15*time.Second)
	defer cancel()
	out, _ := exec.CommandContext(ctx, path, args...).CombinedOutput()
	helpCache[key] = string(out)
	return string(out)
}
