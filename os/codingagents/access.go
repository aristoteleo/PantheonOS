package codingagents

import (
	"os"
	"path/filepath"
	"strings"
)

// Launch identifies one agent process PantheonOS starts.
type Launch struct {
	Agent        string
	Conversation string // the PantheonOS conversation, for turns
	Terminal     string // the PantheonOS terminal session, for terminals
	NodeID       string
	NodeName     string
}

// AccessEnv lets the agent use PantheonOS through the pantheon command:
// the node's key and Hub, and who is asking, so the user can tell an agent's
// requests from their own. Nothing when the node has no key.
func AccessEnv(s Settings, l Launch) []string {
	if s.Access.Key == "" {
		return nil
	}
	env := []string{"PANTHEON_KEY=" + s.Access.Key, "PANTHEON_CLIENT=coding-agent/" + l.Agent,
		"PANTHEON_CODING_AGENT=" + l.Agent}
	if s.Access.HubURL != "" {
		env = append(env, "PANTHEON_HUB_URL="+s.Access.HubURL)
	}
	if l.Conversation != "" {
		env = append(env, "PANTHEON_CODING_CONVERSATION="+l.Conversation)
	}
	if l.NodeID != "" {
		env = append(env, "PANTHEON_NODE_ID="+l.NodeID)
	}
	return env
}

// Note is what PantheonOS tells an agent about the environment it runs in;
// nothing for the Pantheon Agent, which knows it.
func Note(s Settings, l Launch) string {
	if s.Access.Key == "" || l.Agent == PantheonAgent {
		return ""
	}
	where := "a machine"
	if l.NodeName != "" {
		where = l.NodeName
	}
	return "You are running on " + where + ", a node of the user's PantheonOS. The `pantheon` command " +
		"reaches PantheonOS as the user: their desktop and its windows, Apps (notebooks, model services and " +
		"others), their other machines (Fleet nodes) and the files on them, models, and the Pantheon Agent. " +
		"Run `pantheon --help` to see the commands; use them when the task needs that environment, and ask " +
		"the user before changing things outside the task."
}

// cliPath puts the node's own pantheon command first on PATH: the node sets
// PANTHEON_CLI_BIN when it provides one.
func cliPath(dirs []string) []string {
	if bin := os.Getenv("PANTHEON_CLI_BIN"); bin != "" && filepath.IsAbs(bin) {
		return append([]string{bin}, dirs...)
	}
	return dirs
}

// mergeEnv sets each KEY=VALUE of extra over env.
func mergeEnv(env []string, extra ...string) []string {
	if len(extra) == 0 {
		return env
	}
	set := map[string]bool{}
	for _, kv := range extra {
		name, _, _ := strings.Cut(kv, "=")
		set[name] = true
	}
	out := make([]string, 0, len(env)+len(extra))
	for _, kv := range env {
		name, _, _ := strings.Cut(kv, "=")
		if !set[name] {
			out = append(out, kv)
		}
	}
	return append(out, extra...)
}

// LaunchEnv is the full environment of one agent process: the clean
// workspace environment, the agent's needs, the user's variables for it and
// PantheonOS access.
func LaunchEnv(s Settings, l Launch) []string {
	return mergeEnv(UserEnv(s, l.Agent), AccessEnv(s, l)...)
}

// UserEnv is an agent's environment without PantheonOS access: the clean
// workspace environment, the agent's needs and the user's variables for it.
func UserEnv(s Settings, agentID string) []string {
	var own map[string]string
	if c, ok := s.CustomAgent(agentID); ok {
		own = c.Env
	} else {
		own = s.Agents[agentID].Env
	}
	return mergeEnv(AgentEnv(agentID), EnvList(own)...)
}

// LaunchExtras is the part of LaunchEnv a terminal does not already have:
// what a shell on this node must add before starting the agent.
func LaunchExtras(s Settings, l Launch) []string {
	base := map[string]bool{}
	for _, kv := range CleanEnv(workspaceEnv()) {
		base[kv] = true
	}
	var out []string
	for _, kv := range LaunchEnv(s, l) {
		if !base[kv] {
			out = append(out, kv)
		}
	}
	return out
}
