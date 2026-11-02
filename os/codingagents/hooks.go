package codingagents

import (
	"bytes"
	"encoding/json"
	"fmt"
	"net/url"
	"os"
	"path/filepath"
	"sync"
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

// Pi and OpenCode take no hook commands; code of theirs loaded for the launch
// posts the same events instead (none without the launch's hook address):
// SessionStart (Pi: a session started, resumed or switched to), then
// UserPromptSubmit with the prompt and Stop when the agent is done, each with
// the session id. Both files live in the node's state folder.

// piHooks is a Pi extension (-e): Pi's session_start, input and agent_end.
const piHooks = `// Written by PantheonOS: tells the PantheonOS terminal Pi runs in which session
// it is in and when it works.
export default function (pi) {
  const url = process.env.PANTHEON_AGENT_HOOK_URL, token = process.env.PANTHEON_AGENT_HOOK_TOKEN;
  if (!url || !token) return;
  let queue = Promise.resolve();
  const post = (event, ctx, extra) => {
    let body;
    try {
      body = JSON.stringify({ session_id: ctx.sessionManager.getSessionId(), cwd: ctx.sessionManager.getCwd(), ...extra });
    } catch {
      return;
    }
    queue = queue.then(() => fetch(url + "/" + event, { method: "POST", body, signal: AbortSignal.timeout(3000),
      headers: { "X-Pantheon-Hook": token, "Content-Type": "application/json" } })).catch(() => {});
  };
  pi.on("session_start", (_event, ctx) => { post("SessionStart", ctx); });
  pi.on("input", (event, ctx) => {
    const text = String(event.text ?? "");
    if (!text.startsWith("/")) post("UserPromptSubmit", ctx, { prompt: text });
  });
  pi.on("agent_end", (_event, ctx) => { post("Stop", ctx); });
}
`

// opencodeHooks is an OpenCode plugin: its chat.message hook (a prompt,
// with its session, also a resumed one) and session.idle event. A
// sub-agent's sessions (those with a parent) are left out.
const opencodeHooks = `// Written by PantheonOS: tells the PantheonOS terminal OpenCode runs in which
// session it is in and when it works.
export const PantheonTerminal = async () => {
  const url = process.env.PANTHEON_AGENT_HOOK_URL, token = process.env.PANTHEON_AGENT_HOOK_TOKEN;
  if (!url || !token) return {};
  const children = new Set();
  let queue = Promise.resolve();
  const post = (event, payload) => {
    queue = queue.then(() => fetch(url + "/" + event, { method: "POST", body: JSON.stringify(payload),
      signal: AbortSignal.timeout(3000), headers: { "X-Pantheon-Hook": token, "Content-Type": "application/json" } })).catch(() => {});
  };
  return {
    "chat.message": async (input, output) => {
      const id = input?.sessionID;
      if (!id || children.has(id)) return;
      const text = (output?.parts ?? []).filter((p) => p.type === "text" && !p.synthetic).map((p) => p.text).join("\n");
      post("UserPromptSubmit", { session_id: id, prompt: text });
    },
    event: async ({ event }) => {
      const p = event?.properties ?? {};
      if (event?.type === "session.created" && p.info?.parentID) children.add(p.info.id);
      if (event?.type === "session.idle" && p.sessionID && !children.has(p.sessionID)) post("Stop", { session_id: p.sessionID });
    },
  };
};
`

var hookFileMu sync.Mutex

// writeHookFile keeps one of the hook files in the node's state folder, and
// returns its path.
func writeHookFile(name, content string) (string, error) {
	hookFileMu.Lock()
	defer hookFileMu.Unlock()
	dir := filepath.Join(Root(), "hooks")
	path := filepath.Join(dir, name)
	if old, err := os.ReadFile(path); err == nil && bytes.Equal(old, []byte(content)) {
		return path, nil
	}
	if err := os.MkdirAll(dir, 0o700); err != nil {
		return "", err
	}
	tmp := path + ".tmp"
	if err := os.WriteFile(tmp, []byte(content), 0o600); err != nil {
		return "", err
	}
	return path, os.Rename(tmp, path)
}

// PiHookArgs load the hook extension into a Pi terminal launch.
func PiHookArgs() ([]string, error) {
	path, err := writeHookFile("pi-hooks.js", piHooks)
	if err != nil {
		return nil, err
	}
	return []string{"-e", path}, nil
}

// OpenCodeHookConfig is OpenCode's configuration for a terminal launch
// (OPENCODE_CONFIG_CONTENT, which OpenCode lays over the user's own, plugin
// lists joined): config, the one the launch has otherwise ("" for none),
// with the hook plugin added.
func OpenCodeHookConfig(config string) (string, error) {
	path, err := writeHookFile("opencode-hooks.js", opencodeHooks)
	if err != nil {
		return "", err
	}
	doc := map[string]any{}
	if config != "" {
		if err := json.Unmarshal([]byte(config), &doc); err != nil || doc == nil {
			return "", fmt.Errorf("OPENCODE_CONFIG_CONTENT is not a JSON object")
		}
	}
	plugins, _ := doc["plugin"].([]any)
	doc["plugin"] = append(plugins, (&url.URL{Scheme: "file", Path: filepath.ToSlash(path)}).String())
	b, err := json.Marshal(doc)
	return string(b), err
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
	case PantheonAgent, "pi", "opencode":
		switch event {
		case "UserPromptSubmit":
			return "working"
		case "SessionStart", "Stop":
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
