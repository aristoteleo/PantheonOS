// Package codingagents knows the coding-agent CLIs people install next to
// PantheonOS (Claude Code, Codex, Gemini CLI, …): how to find them on a node,
// how to start them in a terminal, and how to run one turn headlessly and read
// its output as events. See docs/apps/coding-agents.md.
package codingagents

import "strings"

// Agent is one catalog entry.
type Agent struct {
	ID    string
	Label string
	// Commands are the executable names that identify the agent; the first
	// one found wins.
	Commands []string
	// TerminalArgs follow the binary in a terminal launch (a subcommand some
	// CLIs need for their interactive interface).
	TerminalArgs []string
	// AutoFlags put the agent in full-auto mode: no approval prompts.
	AutoFlags []string
	// Resume builds the arguments that reopen a session interactively.
	Resume func(id string) []string
	// Headless is set for agents whose conversation mode is supported.
	Headless *Headless
	// ModelFlag selects a model at launch ("--model", "-m").
	ModelFlag string
	// Instructions turns a note from PantheonOS into launch arguments, for
	// agents that take extra system instructions that way.
	Instructions func(text string) []string
	// Install is the command that installs the agent where it is missing.
	Install string
	// Login is the arguments after the binary that sign the agent in, on a
	// node of the given GOOS; nil for an agent without a sign-in of its own.
	// An empty list runs the agent itself, which asks to sign in first.
	Login func(goos string) []string
	// UpdateArgs follow the binary to update the agent in place, for agents
	// that have such a command.
	UpdateArgs []string
	// QuietArgs keep a terminal launch from offering to update the agent
	// itself, for agents whose update ends the session (it then goes through
	// UpdateArgs, when the user asks).
	QuietArgs []string
}

// Headless describes one agent's machine-readable mode.
type Headless struct {
	// Args builds the full argument list for one turn. The prompt goes on
	// stdin unless PromptArg is set, in which case Args already carries it.
	Args      func(t Turn) []string
	PromptArg bool
	// Parser turns the agent's stdout lines into events.
	Parser func() Parser
	// Env is set for headless turns only (KEY=VALUE), each variable only
	// where the user's environment does not set it already.
	Env []string
	// Canvas is what a turn composed on the Agent Canvas can ask of it.
	Canvas Canvas
}

// Canvas is what a canvas turn can ask of one agent; agents_detect reports
// it, and a canvas option the agent cannot take is left out of its turn.
type Canvas struct {
	// Effort is the reasoning levels it takes; nil when it takes none.
	Effort []string `json:"effort"`
	// Research: web research can be switched off for a turn.
	Research bool `json:"research"`
	// Images: reference images reach it.
	Images bool `json:"images"`
	// Skills: it has skills, listed by agents_skills.
	Skills bool `json:"skills"`
	// Roles: sub-agent roles can be defined for a turn; Subagents: its own
	// sub-agents show in its events (launch, parent, end).
	Roles     bool `json:"roles"`
	Subagents bool `json:"subagents"`
	// Models is a short list a composer may offer besides the default.
	Models []string `json:"models"`
	// ImagesNote heads the reference images listed in the message.
	ImagesNote string `json:"-"`
	// PinSkills: skills pinned for a turn are named in its message, with
	// their SKILL.md (Claude Code's are typed as /name instead).
	PinSkills bool `json:"-"`
	// ResearchOff is what a turn with research off tells it; empty: the
	// common words.
	ResearchOff string `json:"-"`
	// SettingsInMessage: a turn's settings go in its message even though
	// the agent takes instructions (Instructions), which it keeps from its
	// session's first turn only.
	SettingsInMessage bool `json:"-"`
	// ShellSandbox: its shell may write only in the turn's folders (Codex's
	// workspace-write sandbox), so a linked conversation's folder is given
	// to it as one more.
	ShellSandbox bool `json:"-"`
	// DirSeparator is a character a folder it is given cannot hold: it
	// splits its folder list there.
	DirSeparator string `json:"-"`
}

// Turn is one headless run.
type Turn struct {
	Prompt  string
	Session string // the agent's own session id; empty starts a new one
	Model   string
	Cwd     string
	Auto    bool
	// A canvas turn, as far as the agent's Canvas allows: reasoning effort
	// (low…max); sub-agent definitions (Claude Code's --agents JSON); more
	// folders the tools may reach; web research off; reference images the
	// agent takes as files.
	Effort    string
	Subagents string
	AddDirs   []string
	NoWeb     bool
	Images    []string
	// Node is the PantheonOS node the turn runs on, for an agent that works
	// on a node it is told (the Pantheon Agent).
	Node string
}

// claudeWebTools are Claude Code's web tools, denied when research is off.
var claudeWebTools = []string{"WebSearch", "WebFetch"}

// codexEffort is Codex's word for an effort level: it stops at xhigh.
func codexEffort(e string) string {
	if e == "max" {
		return "xhigh"
	}
	return e
}

func ids(id string, flag string) []string { return []string{flag, id} }

// PantheonAgent is the catalog id of the user's own Pantheon Agent.
const PantheonAgent = "pantheon"

// Catalog is every agent PantheonOS looks for, in display order.
var Catalog = []Agent{
	{
		// The user's own Pantheon Agent, in the terminal chat of this node's
		// pantheon command. It signs in with the node's key (PantheonOS access).
		ID: PantheonAgent, Label: "Pantheon Agent", Commands: []string{"pantheon"},
		TerminalArgs: []string{"agent", "chat"},
		Resume:       func(id string) []string { return ids(id, "--chat") },
		Headless: &Headless{
			// One turn of the Agent (it runs in the cloud) working on this
			// node, in the turn's folder: its file tools and commands reach
			// the whole node, so a folder outside cwd needs nothing more.
			// The message goes on stdin.
			Args: func(t Turn) []string {
				a := []string{"agent", "chat", "--json"}
				if t.Session != "" {
					a = append(a, "--chat", t.Session)
				}
				if t.Node != "" {
					a = append(a, "--node", t.Node, "--workdir", t.Cwd)
				}
				if t.Model != "" {
					a = append(a, "--model", t.Model)
				}
				return a
			},
			Parser: func() Parser { return &pantheonParser{} },
			// Models are the Agent's tiers, resolved by its own model setup.
			// Its tools cannot be switched per turn (no research off), its
			// image observation needs a vision model the deployment may lack,
			// its skills live in the Agent, and sub-agents are its own
			// profiles (pantheon agent run), not roles a turn defines.
			Canvas: Canvas{Subagents: true, Models: []string{"high", "normal", "low"}},
		},
	},
	{
		ID: "claude", Label: "Claude Code", Commands: []string{"claude"},
		ModelFlag: "--model", Install: "curl -fsSL https://claude.ai/install.sh | bash",
		Login:        func(string) []string { return []string{"auth", "login"} },
		Instructions: func(text string) []string { return []string{"--append-system-prompt", text} },
		AutoFlags:    []string{"--dangerously-skip-permissions"},
		Resume:       func(id string) []string { return ids(id, "--resume") },
		UpdateArgs:   []string{"update"},
		Headless: &Headless{
			Args: func(t Turn) []string {
				a := []string{"-p", "--output-format", "stream-json", "--verbose"}
				if t.Session != "" {
					a = append(a, "--resume", t.Session)
				}
				if t.Model != "" {
					a = append(a, "--model", t.Model)
				}
				if t.Auto {
					a = append(a, "--dangerously-skip-permissions")
				}
				if t.Effort != "" {
					a = append(a, "--effort", t.Effort)
				}
				if t.Subagents != "" {
					a = append(a, "--agents", t.Subagents)
				}
				for _, d := range t.AddDirs {
					a = append(a, "--add-dir", d)
				}
				if t.NoWeb {
					a = append(a, "--disallowedTools", strings.Join(claudeWebTools, ","))
				}
				return a
			},
			Parser: func() Parser { return &claudeParser{} },
			Canvas: Canvas{Effort: []string{"low", "medium", "high", "xhigh", "max"}, Research: true, Images: true,
				Skills: true, Roles: true, Subagents: true, Models: []string{"opus", "sonnet", "haiku"},
				ImagesNote: "Reference images (read each with the Read tool):"},
		},
	},
	{
		ID: "codex", Label: "Codex", Commands: []string{"codex"},
		ModelFlag: "-m", Install: "npm install -g @openai/codex",
		// Its browser sign-in returns to a server on the node's localhost,
		// which a browser elsewhere cannot reach: a Linux node (a server, a
		// workspace) signs in with a code instead.
		Login: func(goos string) []string {
			if goos == "linux" {
				return []string{"login", "--device-auth"}
			}
			return []string{"login"}
		},
		Instructions: func(text string) []string { return []string{"-c", "developer_instructions=" + tomlString(text)} },
		AutoFlags:    []string{"--dangerously-bypass-approvals-and-sandbox"},
		Resume:       func(id string) []string { return []string{"resume", id} },
		UpdateArgs:   []string{"update"},
		QuietArgs:    []string{"-c", "check_for_update_on_startup=false"},
		Headless: &Headless{
			// exec resume takes no --add-dir or --sandbox of its own: exec's,
			// before resume, hold for the resumed turn too. Research off
			// runs in the workspace-write sandbox with its network off
			// (cwd and the --add-dir folders writable), full auto then
			// meaning no approvals rather than no sandbox.
			Args: func(t Turn) []string {
				a := []string{"exec"}
				for _, d := range t.AddDirs {
					a = append(a, "--add-dir", d)
				}
				if t.NoWeb {
					a = append(a, "--sandbox", "workspace-write")
				}
				if t.Session != "" {
					a = append(a, "resume", t.Session)
				}
				for _, img := range t.Images {
					a = append(a, "-i", img)
				}
				a = append(a, "--json", "--skip-git-repo-check")
				if t.Model != "" {
					a = append(a, "-m", t.Model)
				}
				switch {
				case t.Auto && t.NoWeb:
					a = append(a, "-c", `approval_policy="never"`)
				case t.Auto:
					a = append(a, "--dangerously-bypass-approvals-and-sandbox")
				}
				if t.Effort != "" {
					a = append(a, "-c", "model_reasoning_effort="+tomlString(codexEffort(t.Effort)))
				}
				if t.NoWeb {
					a = append(a, "-c", `web_search="disabled"`, "-c", "sandbox_workspace_write.network_access=false")
				}
				return append(a, "-")
			},
			Parser: func() Parser { return &codexParser{} },
			// A resumed thread keeps its first developer instructions
			// (exec resume ignores new ones), so each turn's settings go in
			// its message.
			Canvas: Canvas{Effort: []string{"low", "medium", "high", "xhigh"}, Research: true, Images: true, Skills: true,
				PinSkills: true, SettingsInMessage: true, ShellSandbox: true, ImagesNote: "Reference images (attached to this message):",
				ResearchOff: "Web research is off for this turn: do not search or fetch from the web, and do not reach " +
					"the network any other way either (no curl, wget or other network commands in the shell)."},
		},
	},
	{
		ID: "gemini", Label: "Gemini CLI", Commands: []string{"gemini"},
		ModelFlag: "-m", Install: "npm install -g @google/gemini-cli",
		AutoFlags: []string{"--yolo"},
		// No sign-in command: its own interface asks how to sign in.
		Login:  func(string) []string { return []string{} },
		Resume: func(id string) []string { return ids(id, "--resume") },
		Headless: &Headless{
			PromptArg: true,
			Args: func(t Turn) []string {
				// The prompt joined to its flag: one that starts with a dash
				// is not read as an option.
				a := []string{"--prompt=" + t.Prompt, "-o", "stream-json"}
				if t.Session != "" {
					a = append(a, "--resume", t.Session)
				}
				if t.Model != "" {
					a = append(a, "-m", t.Model)
				}
				if t.Auto {
					a = append(a, "--yolo")
				}
				for _, d := range t.AddDirs {
					a = append(a, "--include-directories", d)
				}
				return a
			},
			Parser: func() Parser { return &geminiParser{} },
			// Headless, Gemini CLI refuses a folder not trusted yet; this
			// trusts it for the run (versions before the check ignore it).
			Env:    []string{"GEMINI_CLI_TRUST_WORKSPACE=true"},
			Canvas: Canvas{Skills: true, PinSkills: true, DirSeparator: ","},
		},
	},
	{
		ID: "cursor", Label: "Cursor Agent", Commands: []string{"cursor-agent"},
		ModelFlag: "--model", Install: "curl https://cursor.com/install -fsS | bash",
		AutoFlags:  []string{"--force"},
		Login:      func(string) []string { return []string{"login"} },
		Resume:     func(id string) []string { return ids(id, "--resume") },
		UpdateArgs: []string{"update"},
		Headless: &Headless{
			PromptArg: true,
			Args: func(t Turn) []string {
				a := []string{"-p", "--output-format", "stream-json", "--trust"}
				if t.Session != "" {
					a = append(a, "--resume", t.Session)
				}
				if t.Model != "" {
					a = append(a, "--model", t.Model)
				}
				if t.Auto {
					a = append(a, "--force")
				}
				for _, d := range t.AddDirs {
					a = append(a, "--add-dir", d)
				}
				// After --, a prompt that starts with a dash is not an option.
				return append(a, "--", t.Prompt)
			},
			Parser: func() Parser { return &cursorParser{} },
			Canvas: Canvas{Images: true, Skills: true, PinSkills: true, ImagesNote: "Reference images (read each one):"},
		},
	},
	{ID: "opencode", Label: "OpenCode", Commands: []string{"opencode"}, ModelFlag: "--model",
		Install: "curl -fsSL https://opencode.ai/install | bash"},
	{ID: "copilot", Label: "GitHub Copilot CLI", Commands: []string{"copilot"},
		AutoFlags: []string{"--allow-all-tools"}, Install: "npm install -g @github/copilot"},
	{ID: "aider", Label: "Aider", Commands: []string{"aider"}, AutoFlags: []string{"--yes-always"}},
	{ID: "goose", Label: "Goose", Commands: []string{"goose"}},
	{ID: "amp", Label: "Amp", Commands: []string{"amp"}},
	{ID: "qwen", Label: "Qwen Code", Commands: []string{"qwen"}, AutoFlags: []string{"--yolo"}, ModelFlag: "-m",
		Install: "npm install -g @qwen-code/qwen-code"},
	{ID: "kimi", Label: "Kimi CLI", Commands: []string{"kimi", "kimi-code"}},
	{ID: "crush", Label: "Crush", Commands: []string{"crush"}},
	{ID: "droid", Label: "Droid", Commands: []string{"droid"}},
	{ID: "cline", Label: "Cline", Commands: []string{"cline"}},
	{ID: "auggie", Label: "Augment", Commands: []string{"auggie"}},
	{ID: "kiro", Label: "Kiro", Commands: []string{"kiro-cli"}, TerminalArgs: []string{"chat"}},
}

// Lookup returns the catalog entry for id.
func Lookup(id string) (Agent, bool) {
	for _, a := range Catalog {
		if a.ID == id {
			return a, true
		}
	}
	return Agent{}, false
}

// TerminalArgv is the argv that opens the agent's interactive interface.
func (a Agent) TerminalArgv(path, resume string, auto bool) []string {
	argv := append([]string{path}, a.TerminalArgs...)
	if resume != "" && a.Resume != nil {
		argv = append(argv, a.Resume(resume)...)
	}
	if auto {
		argv = append(argv, a.AutoFlags...)
	}
	return argv
}

// tomlString quotes s as a TOML basic string (for codex -c key=value).
func tomlString(s string) string {
	r := strings.NewReplacer(`\`, `\\`, `"`, `\"`, "\n", `\n`, "\r", `\r`, "\t", `\t`)
	return `"` + r.Replace(s) + `"`
}
