package shellapp

import (
	"context"
	"fmt"
	"sync"
	"time"

	"github.com/aristoteleo/pantheon-fleet/internal/appsvc"
)

// App is the shell App's state: sessions plus the per-chat session mapping,
// mirroring ShellToolSet (shells / clientid_to_shellid).
type App struct {
	workdir string

	mu           sync.Mutex
	shells       map[string]*session
	chatToShell  map[string]string
	shellCounter int
}

// NewApp builds the shell App rooted in workdir.
func NewApp(workdir string) *App {
	return &App{
		workdir:     workdir,
		shells:      map[string]*session{},
		chatToShell: map[string]string{},
	}
}

// Close shuts every session down (instance stop).
func (a *App) Close() {
	a.mu.Lock()
	shells := make([]*session, 0, len(a.shells))
	for _, s := range a.shells {
		shells = append(shells, s)
	}
	a.shells = map[string]*session{}
	a.chatToShell = map[string]string{}
	a.mu.Unlock()
	for _, s := range shells {
		s.close()
	}
}

// ---- parameter helpers ----------------------------------------------------

func strParam(params map[string]any, name string) string {
	if v, ok := params[name].(string); ok {
		return v
	}
	return ""
}

func intParam(params map[string]any, name string) int {
	switch v := params[name].(type) {
	case float64:
		return int(v)
	case int:
		return v
	}
	return 0
}

// sessionKey mirrors run_command's Auto Mode keying: chat_id, else
// client_id, else "default", read from the framework-injected
// context_variables.
func sessionKey(params map[string]any) string {
	if ctx, ok := params["context_variables"].(map[string]any); ok {
		if v, _ := ctx["chat_id"].(string); v != "" {
			return v
		}
		if v, _ := ctx["client_id"].(string); v != "" {
			return v
		}
	}
	return "default"
}

// truncate mirrors pantheon.utils.truncate.truncate_string (head + tail with
// a count suffix).
func truncate(content string, max int) string {
	if len(content) <= max {
		return content
	}
	suffix := fmt.Sprintf("\n[truncated %d/%d chars]", len(content)-max, len(content))
	available := max - len(suffix) - 20
	if available < 100 {
		simple := max - len(suffix)
		if simple < 0 {
			simple = 0
		}
		return content[:simple] + suffix
	}
	half := available / 2
	return content[:half] + "\n\n...truncated...\n\n" + content[len(content)-half:] + suffix
}

// ---- shell lifecycle ------------------------------------------------------

func (a *App) newShellLocked() (string, *session, error) {
	s, err := startSession(a.workdir)
	if err != nil {
		return "", nil, err
	}
	a.shellCounter++
	id := fmt.Sprintf("go-shell-%d-%s", a.shellCounter, newMarker()[14:22])
	a.shells[id] = s
	return id, s, nil
}

func (a *App) newShell() (map[string]any, error) {
	a.mu.Lock()
	id, _, err := a.newShellLocked()
	a.mu.Unlock()
	if err != nil {
		return nil, err
	}
	return map[string]any{"success": true, "shell_id": id, "initial_output": ""}, nil
}

func (a *App) closeShell(shellID string) map[string]any {
	a.mu.Lock()
	s, ok := a.shells[shellID]
	if ok {
		delete(a.shells, shellID)
	}
	a.mu.Unlock()
	if !ok {
		return map[string]any{"success": false, "error": "Shell not found", "shell_id": shellID}
	}
	s.close()
	return map[string]any{"success": true, "shell_id": shellID}
}

// runInShell mirrors run_command_in_shell: run when command given, drain
// when not; timeout marks status and appends the interrupt warning.
func (a *App) runInShell(shellID, command string, timeoutSec int) map[string]any {
	a.mu.Lock()
	s, ok := a.shells[shellID]
	a.mu.Unlock()
	if !ok {
		return map[string]any{"success": false, "error": "Shell not found", "shell_id": shellID}
	}
	timeout := time.Duration(timeoutSec) * time.Second
	status := "completed"
	var output string
	if command != "" {
		out, finished, err := s.run(command, timeout)
		if err != nil {
			return map[string]any{"success": false, "error": err.Error(), "shell_id": shellID}
		}
		output = out
		if timeoutSec > 0 && !finished {
			status = "timeout"
			output += "\n[Warning] The execution of the command was interrupted because of the timeout. "
		}
	} else {
		out, finished := s.drain(timeout)
		output = out
		if timeoutSec > 0 && !finished {
			status = "timeout"
			output += "\n[Warning] The execution of the command was interrupted because of the timeout. "
		}
	}
	resp := map[string]any{
		"success":  true,
		"shell_id": shellID,
		"status":   status,
		"output":   output,
	}
	if command != "" {
		resp["command"] = command
	}
	return resp
}

// runCommand mirrors ShellToolSet.run_command's Auto Mode.
func (a *App) runCommand(params map[string]any) (map[string]any, error) {
	command := strParam(params, "command")
	shellID := strParam(params, "shell_id")
	timeoutSec := intParam(params, "timeout")
	maxOutput := intParam(params, "max_output")

	var result map[string]any
	if shellID != "" {
		result = a.runInShell(shellID, command, timeoutSec)
	} else {
		key := sessionKey(params)
		a.mu.Lock()
		id, ok := a.chatToShell[key]
		var s *session
		if ok {
			s = a.shells[id]
		}
		if s == nil || !s.alive() {
			if s != nil {
				delete(a.shells, id)
			}
			var err error
			id, s, err = a.newShellLocked()
			if err != nil {
				a.mu.Unlock()
				return nil, err
			}
			a.chatToShell[key] = id
		}
		if !s.idle() {
			// Busy shell: pick any idle one, else a fresh one (Python's
			// _get_available_shell).
			picked := ""
			for sid, sess := range a.shells {
				if sess.idle() && sess.alive() {
					picked = sid
					break
				}
			}
			if picked == "" {
				var err error
				picked, _, err = a.newShellLocked()
				if err != nil {
					a.mu.Unlock()
					return nil, err
				}
			}
			id = picked
		}
		a.mu.Unlock()
		result = a.runInShell(id, command, timeoutSec)
	}

	if ok, _ := result["success"].(bool); !ok {
		return result, nil
	}
	out, _ := result["output"].(string)
	if maxOutput > 0 && len(out) > maxOutput {
		result["output"] = truncate(out, maxOutput)
		result["truncated"] = true
	} else {
		result["truncated"] = false
	}
	return result, nil
}

func (a *App) getShellOutput(shellID string, timeoutSec, maxOutput int) map[string]any {
	if timeoutSec == 0 {
		timeoutSec = 5
	}
	result := a.runInShell(shellID, "", timeoutSec)
	if ok, _ := result["success"].(bool); !ok {
		return result
	}
	truncated := false
	if out, _ := result["output"].(string); maxOutput > 0 && len(out) > maxOutput {
		result["output"] = truncate(out, maxOutput)
		truncated = true
	}
	result["truncated"] = truncated
	return result
}

// ---- the bus surface (shell@1) --------------------------------------------

func optStr(name string) appsvc.Param {
	return appsvc.Param{Type: "str | None", Range: nil, Default: nil, Name: name, Doc: nil}
}
func optInt(name string) appsvc.Param {
	return appsvc.Param{Type: "int | None", Range: nil, Default: nil, Name: name, Doc: nil}
}

// Tools returns the shell@1 surface wired to app. Signatures mirror
// pantheon/toolsets/shell (the committed shell.app.json manifest is the
// parity contract; the Python e2e compares against it).
func Tools(app *App) []*appsvc.Tool {
	return []*appsvc.Tool{
		{
			Name: "run_command",
			Doc: "Run a shell command and return the result.\n\n" +
				"This tool automatically manages shell sessions. Just provide the `command`\n" +
				"to execute. Environment variables and working directory are preserved\n" +
				"across commands in the same session.",
			Inputs: []appsvc.Param{
				optStr("command"), optStr("shell_id"), optInt("timeout"), optInt("max_output"),
			},
			Handler: func(_ context.Context, params map[string]any) (any, error) {
				return app.runCommand(params)
			},
		},
		{
			Name:   "new_shell",
			Doc:    "Create a new shell and return its id.\nUse `run_command` to run commands.",
			Hidden: true,
			Inputs: []appsvc.Param{},
			Handler: func(_ context.Context, _ map[string]any) (any, error) {
				return app.newShell()
			},
		},
		{
			Name:   "close_shell",
			Doc:    "Close a shell.",
			Hidden: true,
			Inputs: []appsvc.Param{
				{Type: "str", Range: nil, Default: appsvc.NotDefined, Name: "shell_id", Doc: nil},
			},
			Handler: func(_ context.Context, params map[string]any) (any, error) {
				return app.closeShell(strParam(params, "shell_id")), nil
			},
		},
		{
			Name:   "get_shell_output",
			Doc:    "Get output from a shell, used to check status of background commands.",
			Hidden: true,
			Inputs: []appsvc.Param{
				{Type: "str", Range: nil, Default: appsvc.NotDefined, Name: "shell_id", Doc: nil},
				{Type: "int", Range: nil, Default: 5, Name: "timeout", Doc: nil},
				optInt("max_output"),
			},
			Handler: func(_ context.Context, params map[string]any) (any, error) {
				return app.getShellOutput(
					strParam(params, "shell_id"),
					intParam(params, "timeout"),
					intParam(params, "max_output"),
				), nil
			},
		},
		{
			Name:   "run_command_in_shell",
			Doc:    "Execute a command or fetch pending output from an existing shell.",
			Hidden: true,
			Inputs: []appsvc.Param{
				{Type: "str", Range: nil, Default: appsvc.NotDefined, Name: "shell_id", Doc: nil},
				optStr("command"), optInt("timeout"),
			},
			Handler: func(_ context.Context, params map[string]any) (any, error) {
				return app.runInShell(
					strParam(params, "shell_id"),
					strParam(params, "command"),
					intParam(params, "timeout"),
				), nil
			},
		},
	}
}
