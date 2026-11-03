package codingagentsapp

import (
	"context"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"regexp"
	"runtime"
	"slices"
	"sort"
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
	// QuotaNote says why the quota is unknown when its provider would not tell.
	QuotaNote string `json:"quota_note,omitempty"`
	// SignInNote says why SignedIn is false when a turn here failed for the
	// agent's sign-in (or its account) since its sign-in last changed.
	SignInNote string `json:"sign_in_note,omitempty"`
	// Canvas is what a canvas turn can ask of an agent with a headless mode.
	Canvas *codingagents.Canvas `json:"canvas,omitempty"`
	// Update is the command line that updates the agent in place, which the
	// desktop runs in a terminal when the user asks. Latest is the newest
	// version its source publishes; UpdateTo is that version when it is
	// newer than the installed one (Update installs it); UpdateNote says
	// why a newer one is not offered.
	Update     string `json:"update,omitempty"`
	Latest     string `json:"latest,omitempty"`
	UpdateTo   string `json:"update_to,omitempty"`
	UpdateNote string `json:"update_note,omitempty"`
	// Login is the command line that signs the agent in, run in a terminal
	// (agent_task_command); absent for an agent without one.
	Login string `json:"login,omitempty"`
	// ModelSource is where an agent that can run on PantheonOS models gets
	// its models (codingagents.SourceSubscription or SourcePantheon); absent
	// for the others.
	ModelSource string `json:"model_source,omitempty"`
}

// Installable is a known agent missing here, with the command that installs it.
type Installable struct {
	ID      string `json:"id"`
	Label   string `json:"label"`
	Command string `json:"command"`
	// Needs is the program the command runs first (npm, curl); Note says
	// what to do when it is not on this node, which then cannot install it.
	Needs string `json:"needs,omitempty"`
	Note  string `json:"note,omitempty"`
}

// installable is a catalog agent's install, with what it needs and whether
// this node has it.
func installable(c codingagents.Agent, which func(string) string) Installable {
	i := Installable{ID: c.ID, Label: c.Label, Command: c.Install}
	if fields := strings.Fields(c.Install); len(fields) > 0 {
		i.Needs = fields[0]
	}
	if i.Needs == "" || which(i.Needs) != "" {
		return i
	}
	switch i.Needs {
	case "npm":
		i.Note = "npm is not on this computer. Install Node.js 18 or newer (it comes with npm), then install " + c.Label + "."
	default:
		i.Note = i.Needs + " is not on this computer. Install it first, then install " + c.Label + "."
	}
	return i
}

// loginCommand is the command line that signs a detected agent in; empty
// for an agent without one.
func loginCommand(f codingagents.Found) string {
	c, ok := codingagents.Lookup(f.ID)
	if !ok || c.Login == nil || f.Custom {
		return ""
	}
	return shellJoin(append([]string{f.Path}, c.Login(runtime.GOOS)...))
}

func (a *App) detect(refresh bool) map[string]any {
	home, _ := os.UserHomeDir()
	s := codingagents.LoadSettings()
	found := codingagents.Apply(codingagents.Detect(refresh), s)
	platform, platformOK := s.PlatformOn()
	infos := make([]AgentInfo, len(found))
	var wg sync.WaitGroup
	for i, f := range found {
		infos[i].Found = f
		if f.Custom || f.Missing {
			continue
		}
		if codingagents.PlatformCapable(f.ID) {
			infos[i].ModelSource = s.ModelSource(f.ID)
		}
		onPlatform := infos[i].ModelSource == codingagents.SourcePantheon
		if c, ok := codingagents.Lookup(f.ID); ok && c.Headless != nil {
			canvas := c.Headless.CanvasNow()
			if onPlatform {
				// The platform's models, not the account's.
				canvas.ModelSource = codingagents.SourcePantheon
				canvas.Models, canvas.ModelLabels, canvas.DefaultModel = nil, nil, ""
				if platformOK {
					m := codingagents.PlatformModels(f.ID, platform)
					canvas.Models, canvas.ModelLabels, canvas.DefaultModel = m.IDs, m.Labels, m.Default
					if slices.Contains(codingagents.RouteAgents, f.ID) {
						canvas.Routes = codingagents.PlatformRoutes(s)
					}
				}
			}
			infos[i].Canvas = &canvas
		}
		infos[i].Login = loginCommand(f)
		wg.Add(1)
		go func() {
			defer wg.Done()
			infos[i].Status = codingagents.Inspect(f)
			infos[i].setUpdate(codingagents.PlanUpdate(f, infos[i].Version))
		}()
		// An agent on PantheonOS spends the platform budget, not its account's quota.
		if fetch := quotaReader(f); fetch != nil && !onPlatform {
			a.refreshQuota(f.ID, fetch, refresh)
		}
	}
	wg.Wait()
	present := map[string]bool{}
	a.mu.Lock()
	for i := range infos {
		present[infos[i].ID] = true
		if infos[i].ModelSource == codingagents.SourcePantheon {
			// Ready with the node's platform key, whatever its own sign-in;
			// without one, the next step says how to get one.
			ready := platformOK
			infos[i].SignedIn = &ready
			if platformOK {
				infos[i].Plan = "PantheonOS models"
			} else {
				infos[i].SignInNote = codingagents.PlatformMissing
			}
			continue
		}
		if l, ok := a.limits[infos[i].ID]; ok {
			infos[i].Limits, infos[i].LimitsAt = l.Limits, l.At
		}
		infos[i].Plan = a.plans[infos[i].ID]
		if f, ok := a.signInFailed[infos[i].ID]; ok {
			if codingagents.CredentialTime(infos[i].ID).After(f.at) {
				delete(a.signInFailed, infos[i].ID)
			} else {
				no := false
				infos[i].SignedIn, infos[i].SignInNote = &no, f.reason
			}
		}
		if _, ok := a.limits[infos[i].ID]; !ok {
			infos[i].QuotaNote = a.quotaNote[infos[i].ID]
		}
	}
	a.mu.Unlock()
	missing := []Installable{}
	for _, c := range codingagents.Catalog {
		if c.Install != "" && !present[c.ID] {
			missing = append(missing, installable(c, codingagents.Which))
		}
	}
	// tasks: installs, sign-ins and updates run through agent_task_command.
	return map[string]any{"success": true, "agents": infos, "installable": missing, "tasks": true, "home": home,
		"pantheon": s.Access.Key != "", "access_off": s.Access.Off, "projects": projectPaths(s.Projects), "workspaces": workspaceList(s.Projects),
		"links":           map[string]any{"send": linksSend, "receive": true, "note": linksNote()},
		"platform_models": platformModels(platformOK)}
}

// platformModels tells the desktop which agents can run on PantheonOS models
// and whether this node can (it has a platform key), else what to do first.
func platformModels(available bool) map[string]any {
	// connect: the desktop may hand it its signed-in user's key (agents_platform).
	out := map[string]any{"agents": codingagents.PlatformAgents, "available": available, "connect": true}
	if !available {
		out["note"] = codingagents.PlatformMissing
	}
	return out
}

// setPlatform stores (or with an empty key, removes) the PantheonOS model
// service key the signed-in desktop hands this node, for its agents on
// PantheonOS models. The key is never given back.
func (a *App) setPlatform(base, key string) map[string]any {
	var p *codingagents.Platform
	if key != "" {
		checked, ok := codingagents.CheckPlatform(base, key)
		if !ok {
			return failure("That is not a PantheonOS model service and key")
		}
		p = &checked
	}
	s, err := codingagents.UpdateSettings(func(s *codingagents.Settings) error {
		s.Platform = p
		return nil
	})
	if err != nil {
		return failure("%v", err)
	}
	return map[string]any{"success": true, "settings": s.Redacted()}
}

// quotaReader is how an agent's account quota is read, for agents whose turns
// do not report it (Claude Code's do, as they run).
func quotaReader(f codingagents.Found) func(context.Context) (codingagents.Quota, error) {
	switch f.ID {
	case "codex":
		path := f.Path
		return func(ctx context.Context) (codingagents.Quota, error) {
			return codingagents.CodexQuota(ctx, path, codingagents.AgentEnv("codex"))
		}
	case "cursor":
		return codingagents.CursorQuota
	case "gemini":
		return codingagents.GeminiQuota
	}
	return nil
}

// refreshQuota reads an agent's quota when the last answer is old, without
// making the caller wait more than a few seconds; one read per agent at a time.
func (a *App) refreshQuota(id string, fetch func(context.Context) (codingagents.Quota, error), force bool) {
	a.mu.Lock()
	fresh := a.limits[id].At > float64(time.Now().Add(-quotaTTL).Unix())
	busy := a.quotaBusy[id]
	if !busy && (force || !fresh) {
		a.quotaBusy[id] = true
	}
	a.mu.Unlock()
	if busy || (fresh && !force) {
		return
	}
	done := make(chan struct{})
	go func() {
		defer close(done)
		ctx, cancel := context.WithTimeout(context.Background(), 20*time.Second)
		defer cancel()
		q, err := fetch(ctx)
		a.mu.Lock()
		defer a.mu.Unlock()
		a.quotaBusy[id] = false
		if q.Plan != "" {
			a.plans[id] = q.Plan
		}
		// The provider says whether it serves the account: as good as a
		// failed sign-in, or no longer one.
		if q.Refused != "" {
			a.signInFailed[id] = signInFailure{reason: agentLabel(id) + " could not sign in: " + q.Refused, at: time.Now(), fromQuota: true}
		} else if f, ok := a.signInFailed[id]; ok && f.fromQuota && err == nil {
			delete(a.signInFailed, id)
		}
		if err != nil || len(q.Limits) == 0 {
			if err != nil {
				a.quotaNote[id] = err.Error()
			}
			return
		}
		delete(a.quotaNote, id)
		a.limits[id] = agentLimits{q.Limits, q.At}
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
	// Prompt is a first message the agent starts with (a new workspace's).
	Prompt string
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
	platform, onPlatform := r.settings.PlatformFor(p.Agent)
	if !onPlatform && r.settings.ModelSource(p.Agent) == codingagents.SourcePantheon {
		return failure("Could not start %s on PantheonOS models: %s", r.found.Label, codingagents.PlatformMissing)
	}
	term := a.newTerminal(p.Agent, r.found.Label, p.Cwd)
	if p.Resume != "" {
		// A session resumed here is the desktop's from now on.
		_ = a.remember(knownSession{Agent: p.Agent, ID: p.Resume, Cwd: p.Cwd})
	}
	launch := codingagents.Launch{Agent: p.Agent, Terminal: term.ID, NodeID: a.NodeID, NodeName: a.NodeName}
	env := codingagents.LaunchExtras(r.settings, launch)
	hooks, hookErr := a.hookServer()
	if hookErr == nil {
		env = append(env, "PANTHEON_AGENT_HOOK_URL="+hooks.url+"/hook/"+term.ID,
			"PANTHEON_AGENT_HOOK_TOKEN="+hooks.token, "PANTHEON_AGENT_TERMINAL="+term.ID)
	}
	var argv []string
	var line string
	prompted := false
	if r.found.Custom {
		c, _ := r.settings.CustomAgent(p.Agent)
		line = c.Command
	} else {
		agent, _ := codingagents.Lookup(p.Agent)
		set := r.settings.Agents[p.Agent]
		argv = agent.TerminalArgv(r.path, p.Resume, p.Auto)
		if p.Agent == codingagents.PantheonAgent && a.NodeID != "" {
			// The Pantheon Agent works on the computer it was started from, in
			// the chosen folder, rather than on the workspace.
			argv = append(argv, "--node", a.NodeID)
			if p.Cwd != "" {
				argv = append(argv, "--workdir", p.Cwd)
			}
		}
		// Before the user's own arguments, which may turn it back on.
		argv = append(argv, agent.QuietArgs...)
		model := codingagents.SubscriptionModel(set.Model)
		if routeArgs, routeEnv, routeModel, isRoute, err := codingagents.RouteLaunch(p.Agent, set.Model, r.settings); isRoute && onPlatform {
			// A Model Services route: through the node's route gateway.
			if err != nil {
				return failure("Could not start %s on the route: %v", r.found.Label, err)
			}
			argv = append(argv, routeArgs...)
			env, model = codingagents.SetEnv(env, routeEnv...), routeModel
		} else if onPlatform {
			models := codingagents.PlatformModels(p.Agent, platform)
			extra, err := codingagents.PlatformArgs(p.Agent, platform, models)
			if err != nil {
				return failure("Could not start %s on PantheonOS models: %v", r.found.Label, err)
			}
			argv = append(argv, extra...)
			model = codingagents.PlatformModelArg(p.Agent, codingagents.PlatformModel(set.Model, models))
		}
		if model != "" && agent.ModelFlag != "" {
			argv = append(argv, agent.ModelFlag, model)
		}
		argv = append(argv, set.Args...)
		if note := codingagents.Note(r.settings, launch); note != "" && agent.Instructions != nil {
			argv = append(argv, agent.Instructions(note)...)
		}
		if hookErr == nil {
			switch p.Agent {
			case "claude":
				if file, err := a.writeTerminalFile(term.ID+".settings.json", codingagents.ClaudeSettings()); err == nil {
					argv = append(argv, "--settings", file)
				}
			case "codex":
				argv = append(argv, codingagents.CodexNotifyArgs()...)
			case "pi":
				if extra, err := codingagents.PiHookArgs(); err == nil {
					argv = append(argv, extra...)
				}
			case "opencode":
				env = withOpenCodeHooks(env)
			}
		}
		if extra := promptArgs(p.Agent, p.Prompt); extra != nil && p.Resume == "" {
			argv = append(argv, extra...)
			prompted = true
		}
		line = shellJoin(argv)
	}
	envFile, err := a.writeTerminalFile(term.ID+".env", []byte(exportLines(env)))
	if err != nil {
		return failure("%v", err)
	}
	command := "( set -a; . " + shellQuote(envFile) + "; set +a; rm -f " + shellQuote(envFile) + "; "
	if unset := codingagents.LaunchUnset(r.settings, launch); len(unset) > 0 {
		// The user's own keys would take the agent off PantheonOS models.
		command += "unset " + strings.Join(unset, " ") + "; "
	}
	switch {
	case hookErr == nil:
		// The agent runs as the subshell's child, so the subshell outlives it
		// and reports how it exited: an agent that updated itself, crashed or
		// was quit leaves only the shell, and the desktop offers it back. Ctrl-C
		// reaches the agent; the subshell waits on. The report itself ignores
		// it: a second Ctrl-C quitting an agent often lands just after the
		// agent let go of the terminal, and would kill the report on its way.
		command += "trap : INT\n" + line + "\npantheon_exit=$?; trap '' INT; " +
			codingagents.ExitReportCommand("pantheon_exit") + "; exit $pantheon_exit )"
	case r.found.Custom:
		// A user's command line may itself be several commands.
		command += line + " )"
	default:
		command += "exec " + line + " )"
	}
	home, _ := os.UserHomeDir()
	return map[string]any{"success": true, "command": command, "argv": argv, "label": r.found.Label, "home": home,
		"terminal_id": term.ID, "prompted": prompted}
}

// withOpenCodeHooks adds the hook plugin to the OpenCode configuration a
// terminal launch gives (the platform's, the user's for the agent, or the
// node's), env unchanged when that configuration cannot take it.
func withOpenCodeHooks(env []string) []string {
	const name = "OPENCODE_CONFIG_CONTENT"
	config, at := os.Getenv(name), -1
	for i, kv := range env {
		if v, ok := strings.CutPrefix(kv, name+"="); ok {
			config, at = v, i
		}
	}
	merged, err := codingagents.OpenCodeHookConfig(config)
	if err != nil {
		return env
	}
	if at < 0 {
		return append(env, name+"="+merged)
	}
	env[at] = name + "=" + merged
	return env
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
			// A list from the desktop names folders only: a project kept keeps
			// its workspaces.
			kept := map[string][]codingagents.Workspace{}
			for _, p := range s.Projects {
				kept[p.Path] = p.Workspaces
			}
			for i, p := range *c.Projects {
				if p.Workspaces == nil {
					(*c.Projects)[i].Workspaces = kept[p.Path]
				}
			}
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

// Folder is one folder the desktop can offer as a project.
type Folder struct {
	Name string `json:"name"`
	Path string `json:"path"`
	Git  bool   `json:"git,omitempty"`
}

// browse lists the folders in path (home when empty), for picking a project
// without typing its path. Hidden folders are left out.
func (a *App) browse(path string) map[string]any {
	home, _ := os.UserHomeDir()
	if strings.TrimSpace(path) == "" {
		path = home
	}
	path = filepath.Clean(expandHome(path))
	entries, err := os.ReadDir(path)
	if err != nil {
		return failure("%s cannot be opened: %v", path, err)
	}
	dirs := []Folder{}
	for _, e := range entries {
		if strings.HasPrefix(e.Name(), ".") {
			continue
		}
		full := filepath.Join(path, e.Name())
		if !e.IsDir() {
			// A link to a folder counts as one.
			if info, err := os.Stat(full); err != nil || !info.IsDir() {
				continue
			}
		}
		_, gitErr := os.Stat(filepath.Join(full, ".git"))
		dirs = append(dirs, Folder{Name: e.Name(), Path: full, Git: gitErr == nil})
		if len(dirs) >= 500 {
			break
		}
	}
	sort.Slice(dirs, func(i, j int) bool { return strings.ToLower(dirs[i].Name) < strings.ToLower(dirs[j].Name) })
	_, gitErr := os.Stat(filepath.Join(path, ".git"))
	return map[string]any{"success": true, "path": path, "parent": filepath.Dir(path), "home": home,
		"git": gitErr == nil, "folders": dirs}
}

var projectName = regexp.MustCompile(`^[^/\\\x00]{1,120}$`)

// createProject makes a new folder for a project, optionally a git
// repository, and adds it to the project list.
func (a *App) createProject(parent, name string, git bool) map[string]any {
	name = strings.TrimSpace(name)
	if !projectName.MatchString(name) || name == "." || name == ".." {
		return failure("A project name is one folder name, without /")
	}
	if strings.TrimSpace(parent) == "" {
		parent, _ = os.UserHomeDir()
	}
	parent = filepath.Clean(expandHome(parent))
	path := filepath.Join(parent, name)
	if _, err := os.Stat(path); err == nil {
		return failure("%s already exists; add it as a folder instead", path)
	}
	if err := os.MkdirAll(path, 0o755); err != nil {
		return failure("%v", err)
	}
	if git {
		ctx, cancel := context.WithTimeout(context.Background(), 20*time.Second)
		defer cancel()
		cmd := exec.CommandContext(ctx, "git", "init", "-q")
		cmd.Dir = path
		cmd.Env = codingagents.AgentEnv("")
		if out, err := cmd.CombinedOutput(); err != nil {
			return failure("The folder was made, but git init failed: %v %s", err, strings.TrimSpace(string(out)))
		}
	}
	projects := append(codingagents.LoadSettings().Projects, codingagents.Project{Path: path})
	res := a.configure(Configure{Projects: &projects})
	if res["success"] == true {
		res["path"] = path
	}
	return res
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

// setAccess stores (or with an empty key, removes: the user turned it off)
// the platform key agents on this node use for the pantheon command. It
// returns the id of a key it replaced, for the desktop to revoke. A key the
// desktop gives on its own (auto, access being on by default) is kept only
// where the node has none and the user did not turn access off: otherwise
// it answers kept, and the desktop revokes the key it made.
func (a *App) setAccess(key string, keyID int64, hubURL string, auto bool) map[string]any {
	if key != "" && !strings.HasPrefix(key, "pbk_") {
		return failure("That is not a PantheonOS platform key")
	}
	var old int64
	kept := false
	s, err := codingagents.UpdateSettings(func(s *codingagents.Settings) error {
		old = s.Access.KeyID
		switch {
		case key == "":
			s.Access = codingagents.Access{Off: true}
		case auto && (s.Access.Key != "" || s.Access.Off):
			kept = true
		default:
			s.Access = codingagents.Access{Key: key, KeyID: keyID, HubURL: strings.TrimRight(hubURL, "/")}
		}
		return nil
	})
	if err != nil {
		return failure("%v", err)
	}
	if kept {
		return map[string]any{"success": true, "kept": true, "settings": s.Redacted()}
	}
	res := map[string]any{"success": true, "settings": s.Redacted()}
	if old != 0 && old != keyID {
		res["replaced_key_id"] = old
	}
	return res
}
