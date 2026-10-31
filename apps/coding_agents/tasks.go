package codingagentsapp

import (
	"fmt"
	"os"
	"runtime"

	"github.com/aristoteleo/pantheonos/os/codingagents"
)

// Tasks are what the desktop runs for an agent in a terminal of its own:
// installing it, signing it in, updating it. The user watches the command
// and answers it (a sign-in asks for a code); its exit status comes back as
// the terminal session's, so the desktop knows when to look for the agent
// again.
const (
	TaskInstall = "install"
	TaskLogin   = "login"
	TaskUpdate  = "update"
)

// taskCommand is the shell command line that runs a task for an agent, for
// pty_open's command, and the terminal session it reports its exit to.
func (a *App) taskCommand(agent, task string) map[string]any {
	if runtime.GOOS == "windows" {
		return failure("Tasks run in a terminal, which this computer's Fleet cannot open: run the command in a terminal there")
	}
	line, label, err := taskLine(agent, task)
	if err != nil {
		return failure("%v", err)
	}
	home, _ := os.UserHomeDir()
	term := a.newTerminal(agent, label, home)
	a.mu.Lock()
	term.Task = task
	a.mu.Unlock()
	hooks, err := a.hookServer()
	if err != nil {
		// Without the report the task still runs; the desktop looks again
		// when asked.
		return map[string]any{"success": true, "command": line, "label": label, "line": line}
	}
	env := []string{"PANTHEON_AGENT_HOOK_URL=" + hooks.url + "/hook/" + term.ID, "PANTHEON_AGENT_HOOK_TOKEN=" + hooks.token}
	envFile, err := a.writeTerminalFile(term.ID+".env", []byte(exportLines(env)))
	if err != nil {
		return failure("%v", err)
	}
	// As an agent's launch: the hook token comes from a file only the user
	// can read, the command runs in a subshell that outlives it and reports
	// how it exited, and the shell left behind has none of it.
	command := "( set -a; . " + shellQuote(envFile) + "; set +a; rm -f " + shellQuote(envFile) + "; " +
		"printf '\\033[2m$ %s\\033[0m\\n' " + shellQuote(line) + "; trap : INT\n" + line +
		"\npantheon_exit=$?; trap '' INT; " + codingagents.ExitReportCommand("pantheon_exit") + "; " +
		`if [ "$pantheon_exit" = 0 ]; then printf '\n\033[32m%s\033[0m\n' 'Done.'; ` +
		`else printf '\n\033[31m%s\033[0m\n' "Failed (exit status $pantheon_exit)."; fi; exit $pantheon_exit )`
	return map[string]any{"success": true, "command": command, "label": label, "line": line, "terminal_id": term.ID}
}

// taskLine is the command line of one task and its title.
func taskLine(agent, task string) (line, label string, err error) {
	c, known := codingagents.Lookup(agent)
	switch task {
	case TaskInstall:
		if !known || c.Install == "" {
			return "", "", fmt.Errorf("PantheonOS cannot install %s", agentLabel(agent))
		}
		if i := installable(c, codingagents.Which); i.Note != "" {
			return "", "", fmt.Errorf("%s", i.Note)
		}
		return c.Install, "Install " + c.Label, nil
	case TaskLogin, TaskUpdate:
		r, err := resolve(agent)
		if err != nil {
			return "", "", err
		}
		if task == TaskLogin {
			if line = loginCommand(r.found); line == "" {
				return "", "", fmt.Errorf("%s has no sign-in of its own", r.found.Label)
			}
			return line, "Sign in to " + r.found.Label, nil
		}
		if line = updateCommand(r.found); line == "" {
			return "", "", fmt.Errorf("%s has no update command", r.found.Label)
		}
		return line, "Update " + r.found.Label, nil
	}
	return "", "", fmt.Errorf("Unknown task %q (install, login or update)", task)
}
