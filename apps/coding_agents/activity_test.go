package codingagentsapp

import (
	"testing"

	"github.com/aristoteleo/pantheonos/os/codingagents"
)

func stateOf(a *App, id string) TerminalSession {
	a.mu.Lock()
	defer a.mu.Unlock()
	return *a.terminals[id]
}

// An agent that reports nothing works while its screen keeps changing and is
// ready once it settles; the state says when it began and what said it.
func TestActivityMovesAnAgentWithoutHooks(t *testing.T) {
	a := NewApp(nil, "", t.TempDir())
	defer a.Close()
	term := a.newTerminal("gemini", "Gemini CLI", "")
	a.onActivity(codingagents.Activity{Terminal: term.ID, Working: true})
	if s := stateOf(a, term.ID); s.State != "working" || s.Source != "activity" || s.Since == 0 {
		t.Fatalf("%+v", s)
	}
	a.onActivity(codingagents.Activity{Terminal: term.ID})
	if s := stateOf(a, term.ID); s.State != "ready" || s.Source != "activity" {
		t.Fatalf("%+v", s)
	}
	a.onActivity(codingagents.Activity{Terminal: term.ID, Notice: "Gemini: Waiting for confirmation"})
	if s := stateOf(a, term.ID); s.State != "waiting" || s.Detail != "Gemini: Waiting for confirmation" {
		t.Fatalf("a notification asking for the user: %+v", s)
	}
	// Settling says nothing about a wait; working again ends it.
	a.onActivity(codingagents.Activity{Terminal: term.ID})
	if s := stateOf(a, term.ID); s.State != "waiting" {
		t.Fatalf("%+v", s)
	}
	a.onActivity(codingagents.Activity{Terminal: term.ID, Working: true})
	if s := stateOf(a, term.ID); s.State != "working" || s.Detail != "" {
		t.Fatalf("%+v", s)
	}
	a.onActivity(codingagents.Activity{Terminal: term.ID, Closed: true})
	if s := stateOf(a, term.ID); s.State != "ended" || s.Source != "exit" {
		t.Fatalf("%+v", s)
	}
}

// Once an agent's own events arrive, its screen says nothing more.
func TestHookedAgentIgnoresActivity(t *testing.T) {
	a := NewApp(nil, "", t.TempDir())
	defer a.Close()
	term := a.newTerminal("claude", "Claude Code", "")
	a.onHook(term.ID, "SessionStart", map[string]any{})
	a.onActivity(codingagents.Activity{Terminal: term.ID, Working: true})
	if s := stateOf(a, term.ID); s.State != "ready" || s.Source != "hook" {
		t.Fatalf("%+v", s)
	}
	a.onHook(term.ID, "UserPromptSubmit", map[string]any{"prompt": "fix it"})
	a.onActivity(codingagents.Activity{Terminal: term.ID})
	if s := stateOf(a, term.ID); s.State != "working" {
		t.Fatalf("a quiet screen ended a turn the hooks say runs: %+v", s)
	}
	a.onHook(term.ID, "Notification", map[string]any{"notification_type": "permission_prompt", "message": "Claude needs your permission to use Bash"})
	if s := stateOf(a, term.ID); s.State != "waiting" || s.Detail != "Claude needs your permission to use Bash" {
		t.Fatalf("%+v", s)
	}
	a.onHook(term.ID, "PostToolUse", map[string]any{"tool_name": "Bash"})
	a.onHook(term.ID, "Stop", nil)
	if s := stateOf(a, term.ID); s.State != "ready" {
		t.Fatalf("%+v", s)
	}
	a.onHook(term.ID, codingagents.ExitHookEvent, map[string]any{"exit_code": float64(2)})
	a.onActivity(codingagents.Activity{Terminal: term.ID, Working: true})
	if s := stateOf(a, term.ID); s.State != "ended" || !s.Exited || s.Source != "exit" || s.Detail != "exit status 2" {
		t.Fatalf("%+v", s)
	}
}

// Codex tells its terminal its state (OSC 7501): that counts as its hooks
// do, and its screen no longer does; its notification says what it waits for.
func TestCodexTerminalState(t *testing.T) {
	a := NewApp(nil, "", t.TempDir())
	defer a.Close()
	term := a.newTerminal("codex", "Codex", "")
	a.onActivity(codingagents.Activity{Terminal: term.ID, State: "working"})
	if s := stateOf(a, term.ID); s.State != "working" || s.Source != "hook" {
		t.Fatalf("%+v", s)
	}
	a.onActivity(codingagents.Activity{Terminal: term.ID, State: "waiting", Detail: "permission"})
	if s := stateOf(a, term.ID); s.State != "waiting" || s.Detail != "Permission" {
		t.Fatalf("%+v", s)
	}
	a.onActivity(codingagents.Activity{Terminal: term.ID, Notice: "Approval requested: touch x"})
	a.onActivity(codingagents.Activity{Terminal: term.ID, State: "waiting", Detail: "permission"})
	if s := stateOf(a, term.ID); s.State != "waiting" || s.Detail != "Approval requested: touch x" {
		t.Fatalf("%+v", s)
	}
	a.onActivity(codingagents.Activity{Terminal: term.ID, State: "ready"})
	a.onActivity(codingagents.Activity{Terminal: term.ID, Working: true})
	if s := stateOf(a, term.ID); s.State != "ready" {
		t.Fatalf("its screen outranked its own state: %+v", s)
	}
}

// An older Codex without terminal states: its screen says when it works, and
// its approval notification that it waits for the user.
func TestCodexTakesActivityAndApprovals(t *testing.T) {
	a := NewApp(nil, "", t.TempDir())
	defer a.Close()
	term := a.newTerminal("codex", "Codex", "")
	a.onActivity(codingagents.Activity{Terminal: term.ID, Working: true})
	a.onActivity(codingagents.Activity{Terminal: term.ID, Notice: "Approval requested: rm -rf build"})
	if s := stateOf(a, term.ID); s.State != "waiting" || s.Detail != "Approval requested: rm -rf build" {
		t.Fatalf("%+v", s)
	}
	a.onActivity(codingagents.Activity{Terminal: term.ID, Working: true})
	a.onHook(term.ID, "Notify", map[string]any{"type": "agent-turn-complete"})
	if s := stateOf(a, term.ID); s.State != "ready" || s.Source != "hook" {
		t.Fatalf("%+v", s)
	}
	// Still read from its screen after its notify arrived.
	a.onActivity(codingagents.Activity{Terminal: term.ID, Working: true})
	if s := stateOf(a, term.ID); s.State != "working" {
		t.Fatalf("%+v", s)
	}
	// A notification that only says a turn finished asks nothing.
	a.onActivity(codingagents.Activity{Terminal: term.ID, Notice: "Agent turn complete"})
	if s := stateOf(a, term.ID); s.State != "working" {
		t.Fatalf("%+v", s)
	}
}

// (Hooks here carry no session id: a session id is remembered on disk, after
// the test.)

// Pi's and OpenCode's hooks: working through a turn and its tools, waiting
// on a permission or a question, ready when done; the prompt titles it
// without starting a turn (Pi's input may never reach a model).
func TestPiAndOpenCodeHookStates(t *testing.T) {
	a := NewApp(nil, "", t.TempDir())
	defer a.Close()
	for _, agent := range []string{"pi", "opencode"} {
		term := a.newTerminal(agent, agent, "")
		a.onHook(term.ID, "SessionStart", map[string]any{})
		a.onHook(term.ID, "Prompt", map[string]any{"prompt": "hello there"})
		if s := stateOf(a, term.ID); s.State != "ready" || s.Title != "hello there" {
			t.Fatalf("%s %+v", agent, s)
		}
		a.onHook(term.ID, "Busy", map[string]any{"tool_name": "bash"})
		if s := stateOf(a, term.ID); s.State != "working" {
			t.Fatalf("%s %+v", agent, s)
		}
		a.onHook(term.ID, "PermissionRequest", map[string]any{"message": "Permission: bash: ls"})
		if s := stateOf(a, term.ID); s.State != "waiting" || s.Detail != "Permission: bash: ls" {
			t.Fatalf("%s %+v", agent, s)
		}
		a.onActivity(codingagents.Activity{Terminal: term.ID, Working: true})
		if s := stateOf(a, term.ID); s.State != "waiting" {
			t.Fatalf("%s: activity overrode a hook: %+v", agent, s)
		}
		a.onHook(term.ID, "Stop", map[string]any{})
		if s := stateOf(a, term.ID); s.State != "ready" {
			t.Fatalf("%s %+v", agent, s)
		}
	}
}
