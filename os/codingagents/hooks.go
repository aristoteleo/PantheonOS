package codingagents

import (
	"encoding/json"
	"os"
	"path/filepath"
)

// A terminal launch reports what the agent is doing through hooks given for
// that launch only; the user's own agent configuration is never changed.
// Each hook posts the agent's event JSON to the node's Coding Agents service
// at $PANTHEON_AGENT_HOOK_URL/<event>.

// hookCommand posts stdin (the event) and prints nothing, whatever happens.
func hookCommand(event string) string {
	return `curl -s -m 3 -X POST -H "X-Pantheon-Hook: $PANTHEON_AGENT_HOOK_TOKEN" --data-binary @- ` +
		`"$PANTHEON_AGENT_HOOK_URL/` + event + `" >/dev/null 2>&1; exit 0`
}

// statusLineCommand forwards Claude Code's status (which carries the
// account's limits) at most every 15 seconds, and draws an empty line.
const statusLineCommand = `s="${TMPDIR:-/tmp}/pantheon-statusline-$PANTHEON_AGENT_TERMINAL"; now=$(date +%s); ` +
	`if [ $((now - $(cat "$s" 2>/dev/null || echo 0))) -ge 15 ]; then echo "$now" > "$s"; ` +
	`curl -s -m 3 -X POST -H "X-Pantheon-Hook: $PANTHEON_AGENT_HOOK_TOKEN" --data-binary @- ` +
	`"$PANTHEON_AGENT_HOOK_URL/StatusLine" >/dev/null 2>&1; else cat >/dev/null; fi; exit 0`

// ClaudeHookEvents are the Claude Code events a terminal launch listens to.
var ClaudeHookEvents = []string{"SessionStart", "UserPromptSubmit", "PreToolUse", "PostToolUse",
	"PermissionRequest", "Notification", "Stop", "SessionEnd"}

// ClaudeSettings is the --settings document for a terminal launch. The
// status line is only taken when the user has none of their own.
func ClaudeSettings() []byte {
	hooks := map[string]any{}
	for _, e := range ClaudeHookEvents {
		hooks[e] = []any{map[string]any{"hooks": []any{map[string]any{"type": "command", "command": hookCommand(e)}}}}
	}
	doc := map[string]any{"hooks": hooks}
	if !userHasStatusLine() {
		doc["statusLine"] = map[string]any{"type": "command", "command": statusLineCommand}
	}
	b, _ := json.Marshal(doc)
	return b
}

func userHasStatusLine() bool {
	for _, name := range []string{"settings.json", "settings.local.json"} {
		b, err := os.ReadFile(filepath.Join(claudeDir(), name))
		if err != nil {
			continue
		}
		var m map[string]any
		if json.Unmarshal(b, &m) == nil && m["statusLine"] != nil {
			return true
		}
	}
	return false
}

// CodexNotifyArgs makes Codex report each finished turn (its only
// launch-time notification).
func CodexNotifyArgs() []string {
	script := `curl -s -m 3 -X POST -H "X-Pantheon-Hook: $PANTHEON_AGENT_HOOK_TOKEN" --data-binary "$1" ` +
		`"$PANTHEON_AGENT_HOOK_URL/Notify" >/dev/null 2>&1; exit 0`
	b, _ := json.Marshal([]string{"sh", "-c", script, "pantheon-notify"})
	return []string{"-c", "notify=" + string(b)}
}

// ExitHookEvent is the event a terminal launch posts once its agent exits.
const ExitHookEvent = "Exit"

// ExitReportCommand posts the exit status held in the shell variable status
// as the Exit event, and prints nothing. The launch line runs it after the
// agent, which every agent's own hooks would miss: an agent that updates
// itself, crashes or is quit leaves the shell behind.
func ExitReportCommand(status string) string {
	return `curl -s -m 3 -X POST -H "X-Pantheon-Hook: $PANTHEON_AGENT_HOOK_TOKEN" ` +
		`--data-binary "{\"exit_code\":$` + status + `}" "$PANTHEON_AGENT_HOOK_URL/` + ExitHookEvent + `" >/dev/null 2>&1`
}

// HookState is what an event says about the agent: working, waiting for
// the user (a permission or a question), ready for the next prompt, or
// ended. Empty when the event says nothing new.
func HookState(agent, event string, payload map[string]any) string {
	switch agent {
	case "claude":
		switch event {
		case "SessionStart", "Stop":
			return "ready"
		case "UserPromptSubmit", "PostToolUse":
			return "working"
		case "PreToolUse":
			if str(payload["tool_name"]) == "AskUserQuestion" {
				return "waiting"
			}
			return "working"
		case "PermissionRequest":
			return "waiting"
		case "Notification":
			switch str(payload["notification_type"]) {
			case "permission_prompt", "elicitation_dialog":
				return "waiting"
			case "idle_prompt":
				return "ready"
			}
		case "SessionEnd":
			return "ended"
		}
	case PantheonAgent:
		switch event {
		case "UserPromptSubmit":
			return "working"
		case "Stop":
			return "ready"
		}
	case "codex":
		if event == "Notify" && str(payload["type"]) == "agent-turn-complete" {
			return "ready"
		}
	}
	return ""
}

// StatusLineLimits reads the account limits Claude Code puts in its status
// line input (rate_limits), in either share or percent form.
func StatusLineLimits(payload map[string]any) map[string]Limit {
	windows := obj(payload["rate_limits"])
	if windows == nil {
		return nil
	}
	out := map[string]Limit{}
	for name, w := range windows {
		m := obj(w)
		if m == nil {
			continue
		}
		used, ok := m["utilization"].(float64)
		if !ok {
			if p, ok := m["used_percentage"].(float64); ok {
				used = p / 100
			}
		}
		if used > 1 {
			used /= 100
		}
		reset := num(m["resets_at"])
		if reset == 0 {
			reset = num(m["resetsAt"])
		}
		out[name] = Limit{Used: used, ResetsAt: reset}
	}
	return out
}
