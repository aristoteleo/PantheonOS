package codingagentsapp

import (
	"context"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"time"

	"github.com/aristoteleo/pantheonos/os/codingagents"
)

const quotaTTL = 5 * time.Minute

// resolved is one agent ready to launch: where it is and how it is set up.
type resolved struct {
	found    codingagents.Found
	path     string
	settings codingagents.Settings
}

// resolve finds an agent with the node's settings applied (a replaced
// command, a custom agent).
func resolve(id string) (resolved, error) {
	s := codingagents.LoadSettings()
	label := id
	if a, ok := codingagents.Lookup(id); ok {
		label = a.Label
	} else if c, ok := s.CustomAgent(id); ok {
		label = c.Label
	} else {
		return resolved{}, fmt.Errorf("Unknown coding agent %q", id)
	}
	for _, refresh := range []bool{false, true} {
		for _, f := range codingagents.Apply(codingagents.Detect(refresh), s) {
			if f.ID != id {
				continue
			}
			if f.Missing {
				return resolved{}, fmt.Errorf("%s: the command set for it, %s, is not on this node", label, f.Path)
			}
			return resolved{found: f, path: f.Path, settings: s}, nil
		}
	}
	return resolved{}, fmt.Errorf("%s is not installed on this node", label)
}

// AgentInfo is a detected agent with its status and last reported quota.
type AgentInfo struct {
	codingagents.Found
	codingagents.Status
	Limits   map[string]codingagents.Limit `json:"limits,omitempty"`
	LimitsAt float64                       `json:"limits_at,omitempty"`
	Plan     string                        `json:"plan,omitempty"`
}

// Installable is a known agent missing here, with the command that installs it.
type Installable struct {
	ID      string `json:"id"`
	Label   string `json:"label"`
	Command string `json:"command"`
}

func (a *App) detect(refresh bool) map[string]any {
	home, _ := os.UserHomeDir()
	s := codingagents.LoadSettings()
	found := codingagents.Apply(codingagents.Detect(refresh), s)
	infos := make([]AgentInfo, len(found))
	var wg sync.WaitGroup
	for i, f := range found {
		infos[i].Found = f
		if f.Custom || f.Missing {
			continue
		}
		wg.Add(1)
		go func() {
			defer wg.Done()
			infos[i].Status = codingagents.Inspect(f)
		}()
		if f.ID == "codex" {
			a.refreshCodexQuota(f.Path, refresh)
		}
	}
	wg.Wait()
	present := map[string]bool{}
	a.mu.Lock()
	for i := range infos {
		present[infos[i].ID] = true
		if l, ok := a.limits[infos[i].ID]; ok {
			infos[i].Limits, infos[i].LimitsAt = l.Limits, l.At
		}
		infos[i].Plan = a.plans[infos[i].ID]
	}
	a.mu.Unlock()
	installable := []Installable{}
	for _, c := range codingagents.Catalog {
		if c.Install != "" && !present[c.ID] {
			installable = append(installable, Installable{c.ID, c.Label, c.Install})
		}
	}
	return map[string]any{"success": true, "agents": infos, "installable": installable, "home": home,
		"pantheon": s.Access.Key != "", "projects": projectPaths(s.Projects)}
}

// refreshCodexQuota asks Codex for its account's limits when the last answer
// is old, without making the caller wait more than a few seconds.
func (a *App) refreshCodexQuota(path string, force bool) {
	a.mu.Lock()
	fresh := a.limits["codex"].At > float64(time.Now().Add(-quotaTTL).Unix())
	a.mu.Unlock()
	if fresh && !force {
		return
	}
	done := make(chan struct{})
	go func() {
		defer close(done)
		if !a.quotaMu.TryLock() {
			return
		}
		defer a.quotaMu.Unlock()
		ctx, cancel := context.WithTimeout(context.Background(), 20*time.Second)
		defer cancel()
		q, err := codingagents.CodexQuota(ctx, path, codingagents.AgentEnv("codex"))
		if err != nil || len(q.Limits) == 0 {
			return
		}
		a.mu.Lock()
		a.limits["codex"] = agentLimits{q.Limits, q.At}
		a.plans["codex"] = q.Plan
		a.mu.Unlock()
	}()
	select {
	case <-done:
	case <-time.After(6 * time.Second):
	}
}

// ---- terminal launches -----------------------------------------------------

// TerminalArgs opens an agent's own interface in a Terminal window.
type TerminalArgs struct {
	Agent  string
	Resume string
	Auto   bool
	Cwd    string
}

// terminalCommand is the shell command line that opens an agent's own
// interface, for pty_open's command. Secrets (the PantheonOS key, the hook
// token) never travel in it: the line reads them from a file only the user
// can read, and removes it. The agent runs in a subshell, so the shell left
// behind when it exits has none of them.
func (a *App) terminalCommand(p TerminalArgs) map[string]any {
	r, err := resolve(p.Agent)
	if err != nil {
		return failure("%v", err)
	}
	term := a.newTerminal(p.Agent, r.found.Label, p.Cwd)
	launch := codingagents.Launch{Agent: p.Agent, Terminal: term.ID, NodeID: a.NodeID, NodeName: a.NodeName}
	env := codingagents.LaunchExtras(r.settings, launch)
	var argv []string
	var line string
	if r.found.Custom {
		c, _ := r.settings.CustomAgent(p.Agent)
		line = c.Command
	} else {
		agent, _ := codingagents.Lookup(p.Agent)
		set := r.settings.Agents[p.Agent]
		argv = agent.TerminalArgv(r.path, p.Resume, p.Auto)
		if set.Model != "" && agent.ModelFlag != "" {
			argv = append(argv, agent.ModelFlag, set.Model)
		}
		argv = append(argv, set.Args...)
		if note := codingagents.Note(r.settings, launch); note != "" && agent.Instructions != nil {
			argv = append(argv, agent.Instructions(note)...)
		}
		if hooks, err := a.hookServer(); err == nil {
			env = append(env, "PANTHEON_AGENT_HOOK_URL="+hooks.url+"/hook/"+term.ID,
				"PANTHEON_AGENT_HOOK_TOKEN="+hooks.token, "PANTHEON_AGENT_TERMINAL="+term.ID)
			switch p.Agent {
			case "claude":
				if file, err := a.writeTerminalFile(term.ID+".settings.json", codingagents.ClaudeSettings()); err == nil {
					argv = append(argv, "--settings", file)
				}
			case "codex":
				argv = append(argv, codingagents.CodexNotifyArgs()...)
			}
		}
		line = shellJoin(argv)
	}
	envFile, err := a.writeTerminalFile(term.ID+".env", []byte(exportLines(env)))
	if err != nil {
		return failure("%v", err)
	}
	command := "( set -a; . " + shellQuote(envFile) + "; set +a; rm -f " + shellQuote(envFile) + "; exec " + line + " )"
	if r.found.Custom {
		// A user's command line may itself be several commands.
		command = "( set -a; . " + shellQuote(envFile) + "; set +a; rm -f " + shellQuote(envFile) + "; " + line + " )"
	}
	home, _ := os.UserHomeDir()
	return map[string]any{"success": true, "command": command, "argv": argv, "label": r.found.Label, "home": home,
		"terminal_id": term.ID}
}

func (a *App) writeTerminalFile(name string, data []byte) (string, error) {
	dir := filepath.Join(a.root, "terminals")
	if err := os.MkdirAll(dir, 0o700); err != nil {
		return "", err
	}
	path := filepath.Join(dir, name)
	return path, os.WriteFile(path, data, 0o600)
}

// exportLines is env as shell assignments.
func exportLines(env []string) string {
	var b strings.Builder
	for _, kv := range env {
		name, value, ok := strings.Cut(kv, "=")
		if !ok || !validName(name) {
			continue
		}
		b.WriteString(name + "=" + shellQuote(value) + "\n")
	}
	return b.String()
}

func validName(s string) bool {
	if s == "" {
		return false
	}
	for i, r := range s {
		if !(r == '_' || r >= 'A' && r <= 'Z' || r >= 'a' && r <= 'z' || i > 0 && r >= '0' && r <= '9') {
			return false
		}
	}
	return true
}

// ---- settings --------------------------------------------------------------

func (a *App) getSettings() map[string]any {
	return map[string]any{"success": true, "settings": codingagents.LoadSettings().Redacted()}
}

// Configure is what agents_configure changes; a field left nil keeps what the
// node has, so the project list and the agents' settings are saved apart.
type Configure struct {
	Agents   map[string]codingagents.AgentSettings
	Custom   *[]codingagents.CustomAgent
	Projects *[]codingagents.Project
}

// configure replaces the agents' settings and the custom agents, or the
// project list; access is set apart (setAccess). A new project must be a
// folder on this node.
func (a *App) configure(c Configure) map[string]any {
	if c.Projects != nil {
		for i, p := range *c.Projects {
			path := filepath.Clean(expandHome(strings.TrimSpace(p.Path)))
			if info, err := os.Stat(path); err != nil || !info.IsDir() {
				return failure("%s is not a folder on this computer", p.Path)
			}
			(*c.Projects)[i].Path = path
		}
	}
	agentsChanged := c.Agents != nil || c.Custom != nil
	s, err := codingagents.UpdateSettings(func(s *codingagents.Settings) error {
		next := *s
		if c.Agents != nil {
			next.Agents = c.Agents
		}
		if c.Custom != nil {
			next.Custom = *c.Custom
		}
		if c.Projects != nil {
			next.Projects = *c.Projects
		}
		if next.Agents == nil {
			next.Agents = map[string]codingagents.AgentSettings{}
		}
		if err := next.Validate(); err != nil {
			return err
		}
		*s = next
		return nil
	})
	if err != nil {
		return failure("%v", err)
	}
	if agentsChanged {
		codingagents.Detect(true)
	}
	return map[string]any{"success": true, "settings": s.Redacted()}
}

func projectPaths(list []codingagents.Project) []string {
	out := make([]string, 0, len(list))
	for _, p := range list {
		out = append(out, p.Path)
	}
	return out
}

func expandHome(p string) string {
	if p == "~" || strings.HasPrefix(p, "~/") {
		if home, err := os.UserHomeDir(); err == nil {
			return filepath.Join(home, strings.TrimPrefix(p, "~"))
		}
	}
	return p
}

// setAccess stores (or with an empty key, removes) the platform key agents
// on this node use for the pantheon command. It returns the id of a key it
// replaced, for the desktop to revoke.
func (a *App) setAccess(key string, keyID int64, hubURL string) map[string]any {
	if key != "" && !strings.HasPrefix(key, "pbk_") {
		return failure("That is not a PantheonOS platform key")
	}
	var old int64
	s, err := codingagents.UpdateSettings(func(s *codingagents.Settings) error {
		old = s.Access.KeyID
		if key == "" {
			s.Access = codingagents.Access{}
			return nil
		}
		s.Access = codingagents.Access{Key: key, KeyID: keyID, HubURL: strings.TrimRight(hubURL, "/")}
		return nil
	})
	if err != nil {
		return failure("%v", err)
	}
	res := map[string]any{"success": true, "settings": s.Redacted()}
	if old != 0 && old != keyID {
		res["replaced_key_id"] = old
	}
	return res
}
