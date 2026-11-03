package codingagents

import (
	"os"
	"slices"
	"strings"
	"testing"
)

// Pi and OpenCode report a whole turn: working from its start through its
// tools, waiting while a permission or a question is open, ready at its end.
// A prompt alone titles the session (Pi's may never reach a model).
func TestPiAndOpenCodeTurnStates(t *testing.T) {
	for _, agent := range []string{"pi", "opencode"} {
		for event, want := range map[string]string{"Busy": "working", "PermissionRequest": "waiting", "Stop": "ready",
			"SessionStart": "ready", "Prompt": ""} {
			if got := HookState(agent, event, nil); got != want {
				t.Errorf("%s %s: %q, want %q", agent, event, got, want)
			}
		}
	}
}

// A background sub-agent's tools run on after Claude Code's turn ended: they
// say nothing about the session.
func TestClaudeSubagentHooksSayNothing(t *testing.T) {
	if got := HookState("claude", "PreToolUse", map[string]any{"tool_name": "Bash", "agent_id": "a1"}); got != "" {
		t.Errorf("sub-agent tool: %q", got)
	}
	if got := HookState("claude", "PreToolUse", map[string]any{"tool_name": "Bash"}); got != "working" {
		t.Errorf("main tool: %q", got)
	}
}

func TestPiHooksFollowTheWholeRun(t *testing.T) {
	t.Setenv("PANTHEON_CODING_AGENTS_DIR", t.TempDir())
	args, err := PiHookArgs()
	if err != nil || len(args) != 2 {
		t.Fatal(args, err)
	}
	b, _ := os.ReadFile(args[1])
	for _, want := range []string{`"agent_start"`, `"tool_execution_start"`, `"agent_settled"`, `"agent_end"`,
		`"ui_prompt_start"`, `"ui_prompt_end"`, `post("Prompt"`, `"PermissionRequest"`} {
		if !strings.Contains(string(b), want) {
			t.Errorf("pi extension lacks %s", want)
		}
	}
}

func TestOpenCodeHooksReportStatusAndPermissions(t *testing.T) {
	t.Setenv("PANTHEON_CODING_AGENTS_DIR", t.TempDir())
	if _, err := OpenCodeHookConfig(""); err != nil {
		t.Fatal(err)
	}
	b, _ := os.ReadFile(Root() + "/hooks/opencode-hooks.js")
	for _, want := range []string{`"session.status"`, `"permission.asked"`, `"question.asked"`, `"permission.replied"`, `post("Busy"`} {
		if !strings.Contains(string(b), want) {
			t.Errorf("opencode plugin lacks %s", want)
		}
	}
}

// Codex sends its notifications, an approval it waits for among them, to
// the terminal as OSC 9 even while the terminal has focus.
func TestCodexNotificationsGoToTheTerminal(t *testing.T) {
	args := CodexNotifyArgs()
	for _, want := range []string{"tui.notifications=true", "tui.notification_method=osc9", "tui.notification_condition=always"} {
		if i := slices.Index(args, want); i < 1 || args[i-1] != "-c" {
			t.Errorf("codex args %q lack -c %s", args, want)
		}
	}
}

func TestActivityRegistry(t *testing.T) {
	var got []Activity
	WatchCommand("( run agent )", "t1", func(a Activity) { got = append(got, a) })
	if CommandTerminal("( run agent )") != "t1" || CommandTerminal("other") != "" || CommandTerminal("") != "" {
		t.Fatal("lookup")
	}
	ReportActivity(Activity{Terminal: "t1", Working: true})
	ReportActivity(Activity{Working: true})
	ForgetTerminal("t1")
	if CommandTerminal("( run agent )") != "" || len(got) != 1 || !got[0].Working {
		t.Fatalf("%v", got)
	}
}
