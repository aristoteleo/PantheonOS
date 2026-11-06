package codingagents

import (
	"os"
	"path/filepath"
	"slices"
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
	return strings.ReplaceAll(accessNote, "{node}", where)
}

// accessNote is the same for every agent: what PantheonOS gives it and the
// commands that matter, each with an example. Short, since some agents take
// it on their command line.
const accessNote = `You are running on {node}, a node of the user's PantheonOS. The ` + "`pantheon`" + ` command in your shell is signed in as the user and reaches PantheonOS. The commands that matter:
- pantheon skills list; pantheon skills view <name>: the user's skills (written procedures). Read a matching one before the task.
- pantheon browser open url=https://example.com, then pantheon browser read (title, text, selectors), click selector=..., type selector=... text=..., screenshot: a real browser the user can watch.
- pantheon desktop windows; pantheon desktop open path=/abs/file; pantheon desktop read window_id=W: windows on the user's PantheonOS desktop (opening one needs the desktop open in their browser). Files open from the user's workspace folder on the Workspace node (usually /workspace/default_workspace): copy one from this machine first with pantheon transfer $PANTHEON_NODE_ID:/abs/file <workspace node>:/workspace/default_workspace/file (pantheon nodes list names the node).
- pantheon apps list; pantheon apps describe <app>; pantheon apps call <app> <tool> key=value: the user's Apps (notebook, files, model services, ...).
- pantheon nodes list; pantheon run --node N -- <command>; pantheon transfer <node>:<path> <node>:<path>: the user's other machines.
- pantheon agent chat -i "<message>": ask the user's Pantheon Agent.
pantheon --help lists every command and pantheon <command> --help explains one; a failing command says what to run next. Use PantheonOS when the task needs it; ask the user before changing things outside the task.`

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
// workspace environment, the agent's needs, the user's variables for it,
// PantheonOS access and, for an agent on PantheonOS models, the platform's
// (over the user's own keys, which it drops).
func LaunchEnv(s Settings, l Launch) []string {
	env := mergeEnv(UserEnv(s, l.Agent), AccessEnv(s, l)...)
	if p, ok := s.PlatformFor(l.Agent); ok {
		set, unset := PlatformEnv(l.Agent, p, PlatformModels(l.Agent, p))
		env = mergeEnv(withoutVars(env, unset), set...)
	}
	return env
}

// TeamEnv is the whole environment of a Team Chat turn's runtime
// (docs/apps/team-chat.md, "The turn's environment"). People other than
// the computer's user may ask a shared agent to run commands, so the turn
// starts from an allow-list, not from the node's environment:
//   - the computer basics (teamBaseVars, LC_*), PATH as AgentEnv sets it,
//     and what the runtime itself needs (ExtraEnv);
//   - on PantheonOS models (model not nil), the platform's variables for
//     model: the turn's own model token and the node's gateway
//     (TurnModelKey), never the user's model key.
//
// Never the user's own variables for the agent (Coding Agents settings:
// from the sixth Phase 3 check none pass, as too many names load code,
// change a program's setup or carry a credential to tell them apart), the
// node's PantheonOS key (PANTHEON_KEY: AccessEnv), a Fleet or Hub
// credential, the ssh agent, or plugin folders such as
// CLAUDE_CODE_PLUGIN_DIRS the node itself was started with.
func TeamEnv(agentID string, model *Platform, m Models) []string {
	var env []string
	for _, kv := range AgentEnv(agentID) {
		name, _, _ := strings.Cut(kv, "=")
		if teamBase(name) {
			env = append(env, kv)
		}
	}
	env = mergeEnv(env, ExtraEnv(agentID)...)
	if model != nil {
		set, unset := PlatformEnv(agentID, *model, m)
		env = mergeEnv(withoutVars(env, unset), set...)
	}
	return env
}

// teamBaseVars are the node's variables a Team Chat turn keeps: who and
// where the user is, language and time zone, the terminal type, certificate
// and proxy settings the network may need, the runtimes' own homes (their
// sign-in and sessions live there; Team Chat starts them without the
// configuration and hooks those homes hold: teamCleanArgs), and Windows'
// system folders.
var teamBaseVars = map[string]bool{"HOME": true, "USER": true, "LOGNAME": true, "SHELL": true, "PATH": true,
	"TMPDIR": true, "TMP": true, "TEMP": true, "LANG": true, "LANGUAGE": true, "TZ": true, "TERM": true,
	"SSL_CERT_FILE": true, "SSL_CERT_DIR": true, "NODE_EXTRA_CA_CERTS": true, "REQUESTS_CA_BUNDLE": true,
	"HTTP_PROXY": true, "HTTPS_PROXY": true, "NO_PROXY": true, "ALL_PROXY": true,
	"CLAUDE_CONFIG_DIR": true, "CODEX_HOME": true,
	"SYSTEMROOT": true, "WINDIR": true, "COMSPEC": true, "PATHEXT": true, "USERPROFILE": true, "USERNAME": true,
	"APPDATA": true, "LOCALAPPDATA": true, "PROGRAMDATA": true, "PROGRAMFILES": true, "PROGRAMFILES(X86)": true,
	"HOMEDRIVE": true, "HOMEPATH": true, "SYSTEMDRIVE": true}

// teamBase: the node's variable name is one a Team Chat turn keeps.
func teamBase(name string) bool {
	up := strings.ToUpper(name)
	return teamBaseVars[up] || strings.HasPrefix(up, "LC_")
}

// LaunchUnset is the variables a shell on this node must unset before
// starting the agent (LaunchEnv leaves them out): an agent on PantheonOS
// models runs without the user's own keys.
func LaunchUnset(s Settings, l Launch) []string {
	if _, ok := s.PlatformFor(l.Agent); !ok {
		return nil
	}
	_, unset := PlatformEnv(l.Agent, Platform{}, Models{})
	return unset
}

// withoutVars is env without the variables named.
func withoutVars(env []string, names []string) []string {
	if len(names) == 0 {
		return env
	}
	out := env[:0:0]
	for _, kv := range env {
		name, _, _ := strings.Cut(kv, "=")
		if !slices.Contains(names, name) {
			out = append(out, kv)
		}
	}
	return out
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
