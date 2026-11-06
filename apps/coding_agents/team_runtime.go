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
// user's own setup where the runtime can (docs/apps/team-chat.md, "What
// Team Chat cannot prevent"): people who may use a shared agent should not
// reach the user's plugins, MCP servers, connectors and hooks through it.
// Codex: --ignore-user-config (no $CODEX_HOME/config.toml: its plugins, MCP
// servers, notify hooks; its sign-in still works). Claude Code: only the
// project's settings (the agent's folder), not the user's (their hooks and
// plugins), and no MCP servers. Older versions without the flag, and other
// runtimes, start as before. Coding Agents' own turns are unchanged.
func teamCleanArgs(agent, path string) []string {
	switch agent {
	case "codex":
		if strings.Contains(runtimeHelp(path, "exec", "--help"), "--ignore-user-config") {
			return []string{"--ignore-user-config"}
		}
	case "claude":
		help := runtimeHelp(path, "--help")
		if strings.Contains(help, "--setting-sources") && strings.Contains(help, "--strict-mcp-config") {
			return []string{"--setting-sources", "project,local", "--strict-mcp-config"}
		}
	}
	return nil
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
