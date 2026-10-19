package shellapp

import (
	"context"
	_ "embed"
	"fmt"
	"regexp"
	"sort"
	"strings"
	"sync"
	"time"

	"github.com/aristoteleo/pantheonos/os/appsvc"
)

// App is the shell App's state: sessions plus the per-chat session mapping,
// mirroring ShellToolSet (shells / clientid_to_shellid).
type App struct {
	workdir string

	mu           sync.Mutex
	shells       map[string]*session
	chatToShell  map[string]string
	// side holds, per owner, the shells its commands ran in while its own
	// shell was busy (sideShell).
	side         map[string][]string
	shellCounter int
	sessions     *appsvc.SessionRegistry
	managed      map[string]appsvc.SessionReceipt
}

// NewApp builds the shell App rooted in workdir.
func NewApp(workdir string) *App {
	a := &App{
		workdir:     workdir,
		shells:      map[string]*session{},
		chatToShell: map[string]string{},
		side:        map[string][]string{},
		managed:     map[string]appsvc.SessionReceipt{},
	}
	a.sessions = appsvc.NewSessionRegistry(func(kind string) (appsvc.SessionResource, error) {
		if kind != "shell" {
			return appsvc.SessionResource{}, fmt.Errorf("unsupported resource session kind")
		}
		a.mu.Lock()
		id, session, err := a.newShellLocked()
		a.mu.Unlock()
		if err != nil {
			return appsvc.SessionResource{}, err
		}
		return appsvc.SessionResource{ID: id, Alive: session.alive, Close: func() error {
			a.closeShell(id)
			select {
			case <-session.waitDone:
				return nil
			case <-time.After(2 * time.Second):
				return fmt.Errorf("shell process has not exited")
			}
		}}, nil
	})
	return a
}

// Close shuts every session down (instance stop).
func (a *App) Close() {
	_ = a.CloseManaged()
}

// BeforeStop retains timed-out commands until their output has been observed.
// HTTP request completion alone does not prove the persistent shell is idle.
func (a *App) BeforeStop() appsvc.DrainReceipt {
	a.mu.Lock()
	defer a.mu.Unlock()
	for _, s := range a.shells {
		if s.alive() && !s.idle() {
			return appsvc.DrainReceipt{Status: "waiting", Message: "Shell commands have pending output; finish or close their sessions before stopping"}
		}
	}
	return appsvc.DrainReceipt{Status: "succeeded", SafeToStop: true}
}

// CloseManaged reports cleanup failure to the generic managed host. The legacy
// builtin keeps its void Close API; its callers do not acquire new semantics.
func (a *App) CloseManaged() error {
	if err := a.sessions.Close(); err != nil {
		return err
	}
	a.mu.Lock()
	shells := make(map[string]*session, len(a.shells))
	for id, s := range a.shells {
		shells[id] = s
	}
	a.mu.Unlock()
	for id, s := range shells {
		s.close()
		select {
		case <-s.waitDone:
			a.mu.Lock()
			delete(a.shells, id)
			for owner, shell := range a.chatToShell {
				if shell == id {
					delete(a.chatToShell, owner)
				}
			}
			a.mu.Unlock()
		case <-time.After(2 * time.Second):
			return fmt.Errorf("shell process has not exited")
		}
	}
	return nil
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

// truncate mirrors pantheonos.utils.truncate.truncate_string (head + tail with
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
	return a.newShellInLocked(a.workdir)
}

func (a *App) newShellInLocked(dir string) (string, *session, error) {
	s, err := startSession(dir)
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
		for key, id := range a.chatToShell {
			if id == shellID {
				delete(a.chatToShell, key)
			}
		}
		for key, ids := range a.side {
			for i, id := range ids {
				if id == shellID {
					a.side[key] = append(ids[:i:i], ids[i+1:]...)
					break
				}
			}
		}
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
	return a.runInShellAs(shellID, command, command, timeoutSec)
}

// runInShellAs runs command but reports shown as the command that ran, so
// the variables run_command's caller set never appear in a result.
func (a *App) runInShellAs(shellID, command, shown string, timeoutSec int) map[string]any {
	a.mu.Lock()
	s, ok := a.shells[shellID]
	managed, leased := a.managed[shellID]
	a.mu.Unlock()
	if !ok {
		return map[string]any{"success": false, "error": "Shell not found", "shell_id": shellID}
	}
	if leased {
		receipt, err := a.sessions.Get(managed.OwnerRef, managed.LeaseID)
		if err != nil || receipt.State != "active" {
			return map[string]any{"success": false, "status": "session_unavailable", "error": "Resource session lease is no longer active", "shell_id": shellID}
		}
	}
	if !s.callMu.TryLock() {
		return map[string]any{"success": false, "status": "busy", "error": "Shell has an active command or output reader", "shell_id": shellID}
	}
	defer s.callMu.Unlock()
	if command != "" && !s.alive() {
		return map[string]any{"success": false, "status": "exited", "error": "Shell exited; create a new session explicitly", "shell_id": shellID}
	}
	if command != "" && !s.idle() {
		return map[string]any{"success": false, "status": "busy", "error": "Shell is busy; fetch pending output before running another command", "shell_id": shellID}
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
	if shown != "" {
		resp["command"] = shown
	}
	return resp
}

var envName = regexp.MustCompile(`^[A-Z_][A-Z0-9_]{0,63}$`)

// commandEnv is run_command's env: variables the caller (the Agent, not the
// model) sets for this command, such as the pantheon command's endpoint and
// token (docs/pantheon-cli.md). They are written as exports into the
// session's input, so they appear in no process listing, and unset when the
// command ends, so a later command never runs with them; work the command
// started in the background keeps its copy. It returns the exports and the
// unset.
func commandEnv(params map[string]any) (string, string, error) {
	raw, ok := params["env"].(map[string]any)
	if !ok || len(raw) == 0 {
		return "", "", nil
	}
	if len(raw) > 16 {
		return "", "", fmt.Errorf("env takes at most 16 variables")
	}
	names := make([]string, 0, len(raw))
	for name := range raw {
		names = append(names, name)
	}
	sort.Strings(names)
	var b strings.Builder
	for _, name := range names {
		value, ok := raw[name].(string)
		if !envName.MatchString(name) || !ok || len(value) > 8192 || strings.ContainsRune(value, 0) {
			return "", "", fmt.Errorf("invalid env variable %q", name)
		}
		b.WriteString("export " + name + "='" + strings.ReplaceAll(value, "'", `'\''`) + "'\n")
	}
	return b.String(), "\nunset " + strings.Join(names, " "), nil
}

// runCommand mirrors ShellToolSet.run_command's Auto Mode.
func (a *App) runCommand(params map[string]any) (map[string]any, error) {
	command := strParam(params, "command")
	exports, unset, err := commandEnv(params)
	if err != nil {
		return map[string]any{"success": false, "error": err.Error()}, nil
	}
	run := command
	if exports != "" && command != "" {
		run = exports + command + unset
	}
	shellID := strParam(params, "shell_id")
	timeoutSec := intParam(params, "timeout")
	maxOutput := intParam(params, "max_output")

	var result map[string]any
	if shellID != "" {
		result = a.runInShellAs(shellID, run, command, timeoutSec)
		a.mu.Lock()
		s, ok := a.shells[shellID]
		_, leased := a.managed[shellID]
		a.mu.Unlock()
		// A conversation's leased session is the dependency's choice, not the
		// model's: like its own shell, it runs a command beside itself when busy.
		if result["status"] == "busy" && command != "" && ok && leased {
			result = a.sideShell("session:"+shellID, s, run, command, timeoutSec)
		}
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
		// A busy session stays bound to its owner. Reusing another owner's
		// idle shell would leak cwd/env and overwrite command/output state;
		// a command that finds its own shell busy runs in a side shell.
		a.mu.Unlock()
		result = a.runInShellAs(id, run, command, timeoutSec)
		if result["status"] == "busy" && command != "" {
			result = a.sideShell(key, s, run, command, timeoutSec)
		}
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

// maxSideShells bounds the shells one owner's parallel commands keep.
const maxSideShells = 4

// sideShell runs a command of an owner whose shell is busy: the model ran
// commands in parallel, or an earlier one is still running. It runs in a new
// shell that starts in the busy shell's directory. The shell closes when the
// command completes; one that timed out stays, under its shell_id, for its
// output.
func (a *App) sideShell(key string, busy *session, run, command string, timeoutSec int) map[string]any {
	a.mu.Lock()
	kept := a.side[key][:0]
	for _, id := range a.side[key] {
		if s, ok := a.shells[id]; ok && s.alive() {
			kept = append(kept, id)
		}
	}
	a.side[key] = kept
	if len(kept) >= maxSideShells {
		a.mu.Unlock()
		return map[string]any{"success": false, "status": "busy", "error": fmt.Sprintf(
			"This conversation already has %d commands running in parallel; wait for one (get_shell_output) or close its shell", len(kept))}
	}
	id, _, err := a.newShellInLocked(busy.dir())
	if err != nil {
		a.mu.Unlock()
		return map[string]any{"success": false, "error": err.Error()}
	}
	a.side[key] = append(a.side[key], id)
	a.mu.Unlock()
	result := a.runInShellAs(id, run, command, timeoutSec)
	if result["status"] == "completed" {
		a.closeShell(id)
		delete(result, "shell_id")
	}
	result["note"] = "Ran in a separate shell because this conversation's shell was busy: it started in the same directory, without variables set there earlier."
	return result
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

//go:embed app.json
var manifestJSON []byte

// Tools returns the shell@1 surface wired to app. Signatures come from the
// embedded app.json — the same manifest `pantheonos.apps check` keeps honest —
// so Go contributes only the handlers; a wiring mismatch errors at startup.
func Tools(app *App) ([]*appsvc.Tool, error) {
	handlers := map[string]appsvc.Handler{
		"run_command": func(_ context.Context, params map[string]any) (any, error) {
			return app.runCommand(params)
		},
		"new_shell": func(_ context.Context, _ map[string]any) (any, error) {
			return app.newShell()
		},
		"close_shell": func(_ context.Context, params map[string]any) (any, error) {
			return app.closeShell(strParam(params, "shell_id")), nil
		},
		"get_shell_output": func(_ context.Context, params map[string]any) (any, error) {
			return app.getShellOutput(
				strParam(params, "shell_id"),
				intParam(params, "timeout"),
				intParam(params, "max_output"),
			), nil
		},
		"run_command_in_shell": func(_ context.Context, params map[string]any) (any, error) {
			return app.runInShell(
				strParam(params, "shell_id"),
				strParam(params, "command"),
				intParam(params, "timeout"),
			), nil
		},
	}
	for name, handler := range appsvc.SessionHandlers(app.sessions, func(receipt appsvc.SessionReceipt) {
		if receipt.SessionID != "" {
			app.mu.Lock()
			app.managed[receipt.SessionID] = receipt
			app.mu.Unlock()
		}
	}) {
		handlers[name] = handler
	}
	return appsvc.ManifestTools(manifestJSON, handlers)
}
