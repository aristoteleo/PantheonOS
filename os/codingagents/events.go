package codingagents

import (
	"encoding/json"
	"fmt"
	"strings"
)

// Event is one step of a conversation, the same shape for every agent.
type Event struct {
	Seq       int64          `json:"seq"`
	Type      string         `json:"type"` // user|session|text|thinking|tool|done
	Text      string         `json:"text,omitempty"`
	Delta     bool           `json:"delta,omitempty"`
	SessionID string         `json:"session_id,omitempty"`
	Model     string         `json:"model,omitempty"`
	ID        string         `json:"id,omitempty"`
	Name      string         `json:"name,omitempty"`
	Input     string         `json:"input,omitempty"`
	Status    string         `json:"status,omitempty"` // running|done|error
	Output    string         `json:"output,omitempty"`
	Error     string         `json:"error,omitempty"`
	Stopped   bool           `json:"stopped,omitempty"` // done: the user stopped the turn
	Usage     map[string]any `json:"usage,omitempty"`
	CostUSD   float64        `json:"cost_usd,omitempty"`
	Time      float64        `json:"time,omitempty"`
	// Parent is the sub-agent launch (its tool id) an event came from;
	// empty for the main agent's own steps.
	Parent string `json:"parent,omitempty"`
	// Subagent is set on a sub-agent's launch tool: the type it runs as.
	// Its Text is the task it was given and Activity what it does now; the
	// launch turns done or error when the sub-agent finishes.
	Subagent string `json:"subagent,omitempty"`
	Activity string `json:"activity,omitempty"`
	// Images and Branch (user events) are the reference images the turn
	// was given and the branch its folder was on.
	Images []string `json:"images,omitempty"`
	Branch string   `json:"branch,omitempty"`
	// A turn's size, the same for every agent (done events): tokens read
	// (cached included), of those served from cache, and written; the
	// tokens in the last request against the model's window; wall time.
	InputTokens   int64 `json:"input_tokens,omitempty"`
	CachedTokens  int64 `json:"cached_tokens,omitempty"`
	OutputTokens  int64 `json:"output_tokens,omitempty"`
	ContextTokens int64 `json:"context_tokens,omitempty"`
	ContextWindow int64 `json:"context_window,omitempty"`
	DurationMs    int64 `json:"duration_ms,omitempty"`
	// Limits (limits events) is the account's subscription quota as the
	// agent reports it: window name -> share used and reset time.
	Limits map[string]Limit `json:"limits,omitempty"`
}

// Limit is one quota window of the agent's account.
type Limit struct {
	Used     float64 `json:"used"`                // 0..1
	ResetsAt int64   `json:"resets_at,omitempty"` // unix seconds
}

func num(v any) int64 {
	f, _ := v.(float64)
	return int64(f)
}

// Parser reads one agent's stdout, a JSON object per line.
type Parser interface {
	Parse(line []byte) []Event
}

const (
	outputCap = 4096
	reportCap = 64 << 10 // a sub-agent's final report
)

func capText(s string) string { return capAt(s, outputCap) }

func capAt(s string, n int) string {
	if len(s) <= n {
		return s
	}
	return s[:n] + "\n…"
}

// oneLine summarises a tool input: the field a person would recognise
// (command, path, pattern…), else compact JSON.
func oneLine(input any) string {
	m, ok := input.(map[string]any)
	if !ok {
		if s, ok := input.(string); ok {
			return firstLine(s)
		}
		b, _ := json.Marshal(input)
		return firstLine(string(b))
	}
	for _, k := range []string{"command", "cmd", "file_path", "path", "filePath", "pattern", "query", "url", "skill", "prompt", "description"} {
		if v, ok := m[k]; ok {
			switch t := v.(type) {
			case string:
				if t != "" {
					return firstLine(t)
				}
			case []any:
				var parts []string
				for _, p := range t {
					parts = append(parts, fmt.Sprint(p))
				}
				return firstLine(strings.Join(parts, " "))
			}
		}
	}
	b, _ := json.Marshal(m)
	return firstLine(string(b))
}

func firstLine(s string) string {
	s = strings.TrimSpace(s)
	if i := strings.IndexByte(s, '\n'); i >= 0 {
		s = s[:i] + " …"
	}
	if len(s) > 300 {
		s = s[:300] + "…"
	}
	return s
}

func str(v any) string {
	s, _ := v.(string)
	return s
}

func obj(v any) map[string]any {
	m, _ := v.(map[string]any)
	return m
}

func decode(line []byte) map[string]any {
	var m map[string]any
	if json.Unmarshal(line, &m) != nil {
		return nil
	}
	return m
}

// contentText flattens a tool_result content field (a string or blocks).
func contentText(v any) string {
	switch t := v.(type) {
	case string:
		return t
	case []any:
		var parts []string
		for _, b := range t {
			if m := obj(b); m != nil && str(m["type"]) == "text" {
				parts = append(parts, str(m["text"]))
			}
		}
		return strings.Join(parts, "\n")
	}
	return ""
}

// ---- Claude Code (-p --output-format stream-json --verbose) ----------------

// Claude Code runs sub-agents through its Agent tool (Task in older
// versions). Every line a sub-agent produces carries parent_tool_use_id, the
// launch's tool id. A launch may run in the background: its tool result then
// only says it started, and a task_notification reports the end. -p keeps
// the process (and the turn) going until background sub-agents end, with a
// result line after each of the main agent's follow-ups.
type claudeParser struct {
	launches map[string]string // launch tool id -> sub-agent type
	ended    map[string]bool   // launches whose end was reported
	results  int
	prev     Event
	session  string // the session reported already
}

func isLaunchTool(name string) bool { return name == "Agent" || name == "Task" }

// subagentReport is the report a foreground sub-agent handed back, without
// the frame Claude Code puts around it for the model: the structured
// result's text blocks when there are some, else the tool result's text
// with the frame cut off.
func subagentReport(result, content any) string {
	var parts []string
	blocks, _ := obj(result)["content"].([]any)
	for _, b := range blocks {
		if t := str(obj(b)["text"]); str(obj(b)["type"]) == "text" && !strings.HasPrefix(strings.TrimSpace(t), "<tools-used-before-summary>") {
			parts = append(parts, t)
		}
	}
	if len(parts) > 0 {
		return strings.TrimSpace(strings.Join(parts, "\n"))
	}
	return stripHandBack(contentText(content))
}

const handBackMark = "The report follows:\n"

// stripHandBack cuts the frame off a sub-agent's report as the model sees
// it: a tools-used note, a preamble ending in "The report follows:", the
// report indented by two spaces, then an agentId line and a usage block.
func stripHandBack(s string) string {
	if i := strings.Index(s, handBackMark); i >= 0 {
		s = s[i+len(handBackMark):]
		var lines []string
		for _, l := range strings.Split(s, "\n") {
			if strings.HasPrefix(l, "agentId: ") || strings.HasPrefix(l, "<usage>") {
				break
			}
			lines = append(lines, strings.TrimPrefix(l, "  "))
		}
		s = strings.Join(lines, "\n")
	} else if i := strings.Index(s, "</tools-used-before-summary>"); i >= 0 {
		s = s[i+len("</tools-used-before-summary>"):]
	}
	return strings.TrimSpace(s)
}

func (p *claudeParser) Parse(line []byte) []Event {
	m := decode(line)
	if m == nil {
		return nil
	}
	if p.launches == nil {
		p.launches, p.ended = map[string]string{}, map[string]bool{}
	}
	parent := str(m["parent_tool_use_id"])
	switch str(m["type"]) {
	case "system":
		switch str(m["subtype"]) {
		case "init":
			// Sent again after each background sub-agent ends; once a turn.
			if id := str(m["session_id"]); id != p.session || id == "" {
				p.session = id
				return []Event{{Type: "session", SessionID: id, Model: str(m["model"])}}
			}
		case "task_progress":
			id := str(m["tool_use_id"])
			if kind, ok := p.launches[id]; ok && !p.ended[id] {
				return []Event{{Type: "tool", ID: id, Subagent: kind, Status: "running",
					Activity: firstLine(str(m["description"]))}}
			}
		case "task_notification":
			id := str(m["tool_use_id"])
			kind, ok := p.launches[id]
			if !ok || p.ended[id] {
				return nil // a background command's, or already reported
			}
			p.ended[id] = true
			e := Event{Type: "tool", ID: id, Subagent: kind, Status: "done", Output: capAt(str(m["summary"]), reportCap)}
			switch str(m["status"]) {
			case "completed":
			case "stopped", "killed":
				e.Status, e.Stopped, e.Error = "error", true, "Stopped"
			default:
				e.Status, e.Error = "error", firstLine(firstNonEmpty(str(m["summary"]), "The sub-agent failed"))
			}
			return []Event{e}
		}
	case "assistant":
		var out []Event
		msg := obj(m["message"])
		blocks, _ := msg["content"].([]any)
		for _, b := range blocks {
			c := obj(b)
			switch str(c["type"]) {
			case "text":
				if t := str(c["text"]); t != "" {
					out = append(out, Event{Type: "text", Text: t, Parent: parent})
				}
			case "thinking":
				if t := str(c["thinking"]); t != "" {
					out = append(out, Event{Type: "thinking", Text: t, Parent: parent})
				}
			case "tool_use":
				e := Event{Type: "tool", ID: str(c["id"]), Name: str(c["name"]),
					Input: oneLine(c["input"]), Status: "running", Parent: parent}
				if in := obj(c["input"]); isLaunchTool(e.Name) && in != nil {
					e.Subagent = firstNonEmpty(str(in["subagent_type"]), "general-purpose")
					e.Input = firstLine(firstNonEmpty(str(in["description"]), e.Subagent))
					e.Text = capText(str(in["prompt"]))
					p.launches[e.ID] = e.Subagent
				}
				out = append(out, e)
			}
		}
		return out
	case "user":
		var out []Event
		msg := obj(m["message"])
		blocks, _ := msg["content"].([]any)
		for _, b := range blocks {
			c := obj(b)
			if str(c["type"]) != "tool_result" {
				continue
			}
			id := str(c["tool_use_id"])
			e := Event{Type: "tool", ID: id, Status: "done", Output: capText(contentText(c["content"])), Parent: parent}
			if c["is_error"] == true {
				e.Status = "error"
			}
			if kind, ok := p.launches[id]; ok {
				if p.ended[id] || str(obj(m["tool_use_result"])["status"]) == "async_launched" {
					continue // the notification has the end, or will
				}
				p.ended[id] = true
				e.Subagent, e.Output = kind, capAt(subagentReport(m["tool_use_result"], c["content"]), reportCap)
				if e.Status == "error" {
					e.Error = firstLine(e.Output)
				}
			}
			out = append(out, e)
		}
		return out
	case "rate_limit_event":
		windows := obj(obj(m["rate_limit_info"])["unifiedWindows"])
		limits := map[string]Limit{}
		for name, w := range windows {
			if wm := obj(w); wm != nil {
				used, _ := wm["utilization"].(float64)
				limits[name] = Limit{Used: used, ResetsAt: num(wm["resetsAt"])}
			}
		}
		if len(limits) > 0 {
			return []Event{{Type: "limits", Limits: limits}}
		}
	case "result":
		u := obj(m["usage"])
		e := Event{Type: "done", Usage: u, DurationMs: num(m["duration_ms"]),
			CachedTokens: num(u["cache_read_input_tokens"]), OutputTokens: num(u["output_tokens"])}
		e.InputTokens = num(u["input_tokens"]) + num(u["cache_creation_input_tokens"]) + e.CachedTokens
		// The last request's prompt is what the conversation now fills.
		if its, _ := u["iterations"].([]any); len(its) > 0 {
			last := obj(its[len(its)-1])
			e.ContextTokens = num(last["input_tokens"]) + num(last["cache_read_input_tokens"]) +
				num(last["cache_creation_input_tokens"]) + num(last["output_tokens"])
		}
		for _, mu := range obj(m["modelUsage"]) {
			if w := num(obj(mu)["contextWindow"]); w > e.ContextWindow {
				e.ContextWindow = w
			}
		}
		if c, ok := m["total_cost_usd"].(float64); ok {
			e.CostUSD = c
		}
		if m["is_error"] == true {
			e.Error = firstNonEmpty(str(m["result"]), str(m["subtype"]), "The turn failed")
		}
		// A follow-up after background sub-agents: the turn is the sum, and
		// its wall time is the runner's to measure.
		if p.results++; p.results > 1 {
			e.InputTokens += p.prev.InputTokens
			e.CachedTokens += p.prev.CachedTokens
			e.OutputTokens += p.prev.OutputTokens
			e.CostUSD = max(e.CostUSD, p.prev.CostUSD)
			e.ContextWindow = max(e.ContextWindow, p.prev.ContextWindow)
			if e.ContextTokens == 0 {
				e.ContextTokens = p.prev.ContextTokens
			}
			e.DurationMs = 0
		}
		p.prev = e
		return []Event{e}
	}
	return nil
}

func firstNonEmpty(v ...string) string {
	for _, s := range v {
		if s != "" {
			return s
		}
	}
	return ""
}

// ---- Codex (exec --json) ---------------------------------------------------

type codexParser struct{}

func (p *codexParser) Parse(line []byte) []Event {
	m := decode(line)
	if m == nil {
		return nil
	}
	switch str(m["type"]) {
	case "thread.started":
		return []Event{{Type: "session", SessionID: str(m["thread_id"])}}
	case "item.started", "item.updated", "item.completed":
		done := str(m["type"]) == "item.completed"
		it := obj(m["item"])
		switch str(it["type"]) {
		case "agent_message":
			if done && str(it["text"]) != "" {
				return []Event{{Type: "text", Text: str(it["text"])}}
			}
		case "reasoning":
			if done && str(it["text"]) != "" {
				return []Event{{Type: "thinking", Text: str(it["text"])}}
			}
		case "command_execution":
			e := Event{Type: "tool", ID: str(it["id"]), Name: "shell", Input: firstLine(unwrapShell(str(it["command"]))), Status: "running"}
			if done {
				e.Status = "done"
				if code, ok := it["exit_code"].(float64); ok && code != 0 {
					e.Status = "error"
				}
				e.Output = capText(str(it["aggregated_output"]))
			}
			return []Event{e}
		case "file_change":
			var paths []string
			changes, _ := it["changes"].([]any)
			for _, c := range changes {
				paths = append(paths, str(obj(c)["path"]))
			}
			e := Event{Type: "tool", ID: str(it["id"]), Name: "edit", Input: firstLine(strings.Join(paths, ", ")), Status: "running"}
			if done {
				e.Status = "done"
				if str(it["status"]) == "failed" {
					e.Status = "error"
				}
			}
			return []Event{e}
		case "mcp_tool_call":
			e := Event{Type: "tool", ID: str(it["id"]), Name: str(it["server"]) + "." + str(it["tool"]),
				Input: oneLine(it["arguments"]), Status: "running"}
			if done {
				e.Status = "done"
				if str(it["status"]) == "failed" {
					e.Status = "error"
				}
			}
			return []Event{e}
		case "web_search":
			if !done {
				return []Event{{Type: "tool", ID: str(it["id"]), Name: "web_search", Input: firstLine(str(it["query"])), Status: "running"}}
			}
			return []Event{{Type: "tool", ID: str(it["id"]), Name: "web_search", Input: firstLine(str(it["query"])), Status: "done"}}
		case "error":
			return []Event{{Type: "text", Text: "⚠ " + str(it["message"])}}
		}
	case "turn.completed":
		u := obj(m["usage"])
		return []Event{{Type: "done", Usage: u, InputTokens: num(u["input_tokens"]),
			CachedTokens: num(u["cached_input_tokens"]), OutputTokens: num(u["output_tokens"])}}
	case "turn.failed":
		return []Event{{Type: "done", Error: firstNonEmpty(str(obj(m["error"])["message"]), "The turn failed")}}
	case "error":
		return []Event{{Type: "done", Error: firstNonEmpty(str(m["message"]), "Codex reported an error")}}
	}
	return nil
}

// ---- Cursor Agent (-p --output-format stream-json) -------------------------

type cursorParser struct{}

func (p *cursorParser) Parse(line []byte) []Event {
	m := decode(line)
	if m == nil {
		return nil
	}
	switch str(m["type"]) {
	case "system":
		if str(m["subtype"]) == "init" {
			return []Event{{Type: "session", SessionID: str(m["session_id"]), Model: str(m["model"])}}
		}
	case "thinking":
		if t := str(m["text"]); t != "" && str(m["subtype"]) == "delta" {
			return []Event{{Type: "thinking", Text: t, Delta: true}}
		}
	case "assistant":
		var out []Event
		blocks, _ := obj(m["message"])["content"].([]any)
		for _, b := range blocks {
			if c := obj(b); str(c["type"]) == "text" && str(c["text"]) != "" {
				out = append(out, Event{Type: "text", Text: str(c["text"])})
			}
		}
		return out
	case "tool_call":
		name, input, output, failed := cursorTool(obj(m["tool_call"]))
		e := Event{Type: "tool", ID: str(m["call_id"]), Name: name, Input: input, Status: "running"}
		if str(m["subtype"]) == "completed" {
			e.Status = "done"
			if failed {
				e.Status = "error"
			}
			e.Output = capText(output)
		}
		return []Event{e}
	case "result":
		u := obj(m["usage"])
		e := Event{Type: "done", Usage: u, DurationMs: num(m["duration_ms"]),
			CachedTokens: num(u["cacheReadTokens"]), OutputTokens: num(u["outputTokens"])}
		e.InputTokens = num(u["inputTokens"]) + num(u["cacheWriteTokens"]) + e.CachedTokens
		if m["is_error"] == true {
			e.Error = firstNonEmpty(str(m["result"]), "The turn failed")
		}
		return []Event{e}
	}
	return nil
}

// cursorTool unwraps {"<kind>ToolCall": {"args": …, "result": …}}.
func cursorTool(tc map[string]any) (name, input, output string, failed bool) {
	for k, v := range tc {
		if !strings.HasSuffix(k, "ToolCall") {
			continue
		}
		name = strings.TrimSuffix(k, "ToolCall")
		body := obj(v)
		input = oneLine(body["args"])
		res := obj(body["result"])
		if s := obj(res["success"]); s != nil {
			output = firstNonEmpty(str(s["interleavedOutput"]), str(s["stdout"]), str(s["content"]))
			if code, ok := s["exitCode"].(float64); ok && code != 0 {
				failed = true
			}
		} else if f := res["failure"]; f != nil {
			failed = true
			b, _ := json.Marshal(f)
			output = string(b)
		} else if res["error"] != nil {
			failed = true
			output = fmt.Sprint(res["error"])
		}
		return
	}
	return "tool", "", "", false
}

// ---- Gemini CLI (-p -o stream-json) ----------------------------------------

type geminiParser struct{}

func (p *geminiParser) Parse(line []byte) []Event {
	m := decode(line)
	if m == nil {
		return nil
	}
	switch str(m["type"]) {
	case "init":
		return []Event{{Type: "session", SessionID: str(m["session_id"]), Model: str(m["model"])}}
	case "message":
		if str(m["role"]) == "assistant" && str(m["content"]) != "" {
			return []Event{{Type: "text", Text: str(m["content"]), Delta: m["delta"] == true}}
		}
	case "tool_use":
		return []Event{{Type: "tool", ID: str(m["tool_id"]), Name: str(m["tool_name"]),
			Input: oneLine(m["parameters"]), Status: "running"}}
	case "tool_result":
		e := Event{Type: "tool", ID: str(m["tool_id"]), Status: "done", Output: capText(str(m["output"]))}
		if str(m["status"]) == "error" {
			e.Status = "error"
			e.Output = capText(firstNonEmpty(str(m["output"]), str(obj(m["error"])["message"])))
		}
		return []Event{e}
	case "error":
		return []Event{{Type: "text", Text: "⚠ " + str(m["message"])}}
	case "result":
		st := obj(m["stats"])
		e := Event{Type: "done", Usage: st, InputTokens: num(st["input_tokens"]), CachedTokens: num(st["cached"]),
			OutputTokens: num(st["output_tokens"]), DurationMs: num(st["duration_ms"])}
		if str(m["status"]) == "error" {
			e.Error = firstNonEmpty(str(obj(m["error"])["message"]), "The turn failed")
		}
		return []Event{e}
	}
	return nil
}

// unwrapShell shows the command Codex ran rather than the login shell it
// wraps every command in: /bin/zsh -lc 'ls -la' reads as ls -la.
func unwrapShell(cmd string) string {
	for _, flag := range []string{" -lc ", " -c "} {
		shell, rest, ok := strings.Cut(cmd, flag)
		if !ok || strings.ContainsAny(shell, " '\"") || !strings.HasSuffix(shell, "sh") {
			continue
		}
		if len(rest) >= 2 && rest[0] == '\'' && rest[len(rest)-1] == '\'' {
			return strings.ReplaceAll(rest[1:len(rest)-1], `'\''`, "'")
		}
		return rest
	}
	return cmd
}
