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
}

// Headless describes one agent's machine-readable mode.
type Headless struct {
	// Args builds the full argument list for one turn. The prompt goes on
	// stdin unless PromptArg is set, in which case Args already carries it.
	Args      func(t Turn) []string
	PromptArg bool
	// Parser turns the agent's stdout lines into events.
	Parser func() Parser
}

// Turn is one headless run.
type Turn struct {
	Prompt  string
	Session string // the agent's own session id; empty starts a new one
	Model   string
	Cwd     string
	Auto    bool
}

func ids(id string, flag string) []string { return []string{flag, id} }

// Catalog is every agent PantheonOS looks for, in display order.
var Catalog = []Agent{
	{
		ID: "claude", Label: "Claude Code", Commands: []string{"claude"},
		ModelFlag: "--model", Install: "curl -fsSL https://claude.ai/install.sh | bash",
		Instructions: func(text string) []string { return []string{"--append-system-prompt", text} },
		AutoFlags: []string{"--dangerously-skip-permissions"},
		Resume:    func(id string) []string { return ids(id, "--resume") },
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
				return a
			},
			Parser: func() Parser { return &claudeParser{} },
		},
	},
	{
		ID: "codex", Label: "Codex", Commands: []string{"codex"},
		ModelFlag: "-m", Install: "npm install -g @openai/codex",
		Instructions: func(text string) []string { return []string{"-c", "developer_instructions=" + tomlString(text)} },
		AutoFlags: []string{"--dangerously-bypass-approvals-and-sandbox"},
		Resume:    func(id string) []string { return []string{"resume", id} },
		Headless: &Headless{
			Args: func(t Turn) []string {
				a := []string{"exec"}
				if t.Session != "" {
					a = append(a, "resume", t.Session)
				}
				a = append(a, "--json", "--skip-git-repo-check")
				if t.Model != "" {
					a = append(a, "-m", t.Model)
				}
				if t.Auto {
					a = append(a, "--dangerously-bypass-approvals-and-sandbox")
				}
				return append(a, "-")
			},
			Parser: func() Parser { return &codexParser{} },
		},
	},
	{
		ID: "gemini", Label: "Gemini CLI", Commands: []string{"gemini"},
		ModelFlag: "-m", Install: "npm install -g @google/gemini-cli",
		AutoFlags: []string{"--yolo"},
		Resume:    func(id string) []string { return ids(id, "--resume") },
		Headless: &Headless{
			PromptArg: true,
			Args: func(t Turn) []string {
				a := []string{"-p", t.Prompt, "-o", "stream-json"}
				if t.Session != "" {
					a = append(a, "--resume", t.Session)
				}
				if t.Model != "" {
					a = append(a, "-m", t.Model)
				}
				if t.Auto {
					a = append(a, "--yolo")
				}
				return a
			},
			Parser: func() Parser { return &geminiParser{} },
		},
	},
	{
		ID: "cursor", Label: "Cursor Agent", Commands: []string{"cursor-agent"},
		ModelFlag: "--model", Install: "curl https://cursor.com/install -fsS | bash",
		AutoFlags: []string{"--force"},
		Resume:    func(id string) []string { return ids(id, "--resume") },
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
				return append(a, t.Prompt)
			},
			Parser: func() Parser { return &cursorParser{} },
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
