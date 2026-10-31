package codingagents

import (
	"bytes"
	"encoding/json"
	"fmt"
	"sort"
	"strings"
)

// Event is one step of a conversation, the same shape for every agent.
type Event struct {
	Seq       int64  `json:"seq"`
	Type      string `json:"type"` // user|session|text|thinking|tool|done|limits|link
	Text      string `json:"text,omitempty"`
	Delta     bool   `json:"delta,omitempty"`
	SessionID string `json:"session_id,omitempty"`
	Model     string `json:"model,omitempty"`
	ID        string `json:"id,omitempty"`
	Name      string `json:"name,omitempty"`
	Input     string `json:"input,omitempty"`
	// Kind is what a tool call does, the same words for every agent (see
	// the Kind constants); Path is the file a read or write is about, as the
	// agent gave it. Both come with the event that names the tool.
	Kind    string         `json:"kind,omitempty"`
	Path    string         `json:"path,omitempty"`
	Status  string         `json:"status,omitempty"` // running|done|error
	Output  string         `json:"output,omitempty"`
	Error   string         `json:"error,omitempty"`
	Stopped bool           `json:"stopped,omitempty"` // done: the user stopped the turn
	Usage   map[string]any `json:"usage,omitempty"`
	CostUSD float64        `json:"cost_usd,omitempty"`
	Time    float64        `json:"time,omitempty"`
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
	// Linked groups on the Agent Canvas: a user event of a turn a message
	// from another group started has From (and ID, the message's id); a
	// link event is one message sent (Dir out, To) or waiting here (Dir in,
	// From), with its Status, Bytes and Hop (its place in an exchange).
	From  *LinkEnd `json:"from,omitempty"`
	To    *LinkEnd `json:"to,omitempty"`
	Dir   string   `json:"dir,omitempty"`
	Hop   int      `json:"hop,omitempty"`
	Bytes int      `json:"bytes,omitempty"`
}

// LinkEnd is one side of a link between two conversations: the
// conversation, its node (empty: this one) and its group's name.
type LinkEnd struct {
	ConversationID string `json:"conversation_id"`
	NodeID         string `json:"node_id,omitempty"`
	Name           string `json:"name,omitempty"`
}

// Limit is one quota window of the agent's account.
type Limit struct {
	Used     float64 `json:"used"`                // 0..1
	ResetsAt int64   `json:"resets_at,omitempty"` // unix seconds
}

// What a tool call does (Event.Kind). Searching means the web; a search
// through the code is other, and so is anything a kind does not name.
const (
	KindRead   = "read"
	KindWrite  = "write"
	KindShell  = "shell"
	KindSearch = "search"
	KindFetch  = "fetch"
	KindSkill  = "skill"
	KindAgent  = "agent"
	KindOther  = "other"
)

// skillFile is the skill a SKILL.md path belongs to, or "": the path must
// be <…>/skills/<name>/SKILL.md (or skills/.system/<name>, Codex's own).
func skillFile(path string) string {
	parts := strings.Split(strings.ReplaceAll(path, `\`, "/"), "/")
	n := len(parts)
	if n < 3 || parts[n-1] != "SKILL.md" {
		return ""
	}
	name, parent := parts[n-2], parts[n-3]
	if parent == ".system" && n >= 4 {
		parent = parts[n-4]
	}
	if parent != "skills" || name == "" || name == "." || name == ".." {
		return ""
	}
	return name
}

// shellReaders are the commands an agent reads a file with in a shell.
var shellReaders = map[string]bool{"cat": true, "sed": true, "head": true, "tail": true, "nl": true,
	"less": true, "more": true, "bat": true, "type": true, "Get-Content": true}

// shellSkill is the skill a shell command reads (an agent without a skill
// tool opens a skill's SKILL.md itself): its name and the path as written.
// Each command of a list or pipeline counts on its own; only a plain read
// does (sed only with -n and not -i), never one with an output redirection.
func shellSkill(cmd string) (name, path string) {
	for _, seg := range shellCommands(cmd) {
		if seg.redirect || len(seg.words) < 2 || !shellReaders[seg.words[0]] {
			continue
		}
		if seg.words[0] == "sed" && !sedPrints(seg.words[1:]) {
			continue
		}
		for _, w := range seg.words[1:] {
			if n := skillFile(w); n != "" {
				return n, w
			}
		}
	}
	return "", ""
}

// sedPrints is whether sed's arguments only print: -n, and no in-place edit.
func sedPrints(args []string) bool {
	quiet := false
	for _, a := range args {
		if strings.HasPrefix(a, "--in-place") || strings.HasPrefix(a, "-") && !strings.HasPrefix(a, "--") && strings.Contains(a, "i") {
			return false
		}
		if a == "-n" || a == "--quiet" || a == "--silent" || strings.HasPrefix(a, "-") && !strings.HasPrefix(a, "--") && strings.Contains(a, "n") {
			quiet = true
		}
	}
	return quiet
}

// shellCommand is one simple command of a shell line: its words, unquoted,
// and whether it redirects its output.
type shellCommand struct {
	words    []string
	redirect bool
}

// shellCommands splits a shell line into its simple commands (at ;, &, |,
// && and ||), with words unquoted as the shell would ('…', "…" and \).
// Good enough to read the commands an agent runs, not a full shell.
func shellCommands(line string) []shellCommand {
	var out []shellCommand
	var cur shellCommand
	var word strings.Builder
	inWord := false
	endWord := func() {
		if inWord {
			cur.words = append(cur.words, word.String())
			word.Reset()
			inWord = false
		}
	}
	endCommand := func() {
		endWord()
		if len(cur.words) > 0 {
			out = append(out, cur)
		}
		cur = shellCommand{}
	}
	for i := 0; i < len(line); i++ {
		c := line[i]
		switch {
		case c == '\\' && i+1 < len(line):
			i++
			word.WriteByte(line[i])
			inWord = true
		case c == '\'':
			j := strings.IndexByte(line[i+1:], '\'')
			if j < 0 {
				j = len(line) - i - 1
			}
			word.WriteString(line[i+1 : i+1+j])
			i += j + 1
			inWord = true
		case c == '"':
			for i++; i < len(line) && line[i] != '"'; i++ {
				if line[i] == '\\' && i+1 < len(line) && strings.IndexByte(`"\$`+"`", line[i+1]) >= 0 {
					i++
				}
				word.WriteByte(line[i])
			}
			inWord = true
		case c == ' ' || c == '\t' || c == '\n':
			endWord()
		case c == ';' || c == '&' || c == '|':
			endCommand()
		case c == '>':
			// n> names a descriptor: 2> (stderr) writes nothing the
			// command read.
			fd := word.String()
			if inWord && strings.Trim(fd, "0123456789") == "" {
				word.Reset()
				inWord = false
			} else {
				endWord()
				fd = "1"
			}
			if i+1 < len(line) && line[i+1] == '>' {
				i++
			}
			if i+1 < len(line) && line[i+1] == '&' {
				for i++; i+1 < len(line) && line[i+1] >= '0' && line[i+1] <= '9'; i++ {
				}
			}
			cur.redirect = cur.redirect || fd != "2"
		default:
			word.WriteByte(c)
			inWord = true
		}
	}
	endCommand()
	return out
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
	for _, k := range []string{"command", "cmd", "file_path", "path", "filePath", "pattern", "query", "searchTerm", "url", "skill", "prompt", "description"} {
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

// inputKeys are the arguments that say most about a tool call, best first:
// what a search looks for comes before the folder it searches.
var inputKeys = []string{"command", "cmd", "pattern", "globPattern", "query", "searchTerm", "url", "toolName",
	"file_path", "path", "filePath", "skill", "name", "prompt", "description"}

// toolInput summarises the arguments of a tool call for agents other than
// Claude Code (whose inputs oneLine keeps as they were): the most telling
// argument, else the first short text argument by name, else compact JSON.
func toolInput(input any) string {
	m, ok := input.(map[string]any)
	if !ok {
		return oneLine(input)
	}
	for _, k := range inputKeys {
		switch v := m[k].(type) {
		case string:
			if v != "" {
				return firstLine(v)
			}
		case []any:
			if len(v) > 0 {
				return oneLine(map[string]any{"command": v})
			}
		}
	}
	keys := make([]string, 0, len(m))
	for k := range m {
		keys = append(keys, k)
	}
	sort.Strings(keys)
	for _, k := range keys {
		if t, ok := m[k].(string); ok && t != "" && len(t) <= 200 {
			return firstLine(t)
		}
	}
	return oneLine(m)
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

// claudeKind is what one of Claude Code's tools does, and the file it is about.
func claudeKind(name string, in map[string]any) (kind, path string) {
	switch name {
	case "Read":
		return KindRead, str(in["file_path"])
	case "Write", "Edit", "MultiEdit":
		return KindWrite, str(in["file_path"])
	case "NotebookEdit":
		return KindWrite, str(in["notebook_path"])
	case "Bash":
		return KindShell, ""
	case "WebSearch":
		return KindSearch, ""
	case "WebFetch":
		return KindFetch, ""
	case "Skill":
		return KindSkill, ""
	}
	if isLaunchTool(name) {
		return KindAgent, ""
	}
	return KindOther, ""
}

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
				e.Kind, e.Path = claudeKind(e.Name, obj(c["input"]))
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

// Codex reads files and skills through its shell, so a command stays a
// shell step unless it opens a skill's SKILL.md. A file change that touches
// several files is one write event per file: the first keeps the item's id,
// the others add #2, #3, … to it. Its sub-agents are not in the stream (see
// testdata/README.md): a wait for them is an other step.
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
		status := "running"
		if done {
			status = "done"
			if str(it["status"]) == "failed" {
				status = "error"
			}
		}
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
			cmd := unwrapShell(str(it["command"]))
			e := Event{Type: "tool", ID: str(it["id"]), Name: "shell", Input: firstLine(cmd), Kind: KindShell, Status: "running"}
			if name, path := shellSkill(cmd); name != "" {
				e.Kind, e.Input, e.Path = KindSkill, name, path
			}
			if done {
				e.Status = "done"
				if code, ok := it["exit_code"].(float64); ok && code != 0 {
					e.Status = "error"
				}
				e.Output = capText(str(it["aggregated_output"]))
			}
			return []Event{e}
		case "file_change":
			var out []Event
			changes, _ := it["changes"].([]any)
			for i, c := range changes {
				path := str(obj(c)["path"])
				id := str(it["id"])
				if i > 0 {
					id = fmt.Sprintf("%s#%d", id, i+1)
				}
				out = append(out, Event{Type: "tool", ID: id, Name: "edit", Input: firstLine(path), Kind: KindWrite, Path: path, Status: status})
			}
			return out
		case "mcp_tool_call":
			return []Event{{Type: "tool", ID: str(it["id"]), Name: str(it["server"]) + "." + str(it["tool"]),
				Input: toolInput(it["arguments"]), Kind: KindOther, Status: status}}
		case "web_search":
			// Its item carries "id" twice (the item's, then the search
			// call's); the item's is the one every other item uses. What it
			// did is known only once it completes: a search, or a page
			// opened (fetch), so a call that opened a page starts as a
			// search and ends as a fetch.
			e := Event{Type: "tool", ID: firstItemID(line), Name: "web_search", Input: firstLine(str(it["query"])), Kind: KindSearch, Status: "running"}
			if done {
				e.Status = "done"
				if a := obj(it["action"]); str(a["type"]) == "open_page" || str(a["type"]) == "find_in_page" {
					e.Kind, e.Input = KindFetch, firstLine(str(a["url"]))
				}
			}
			return []Event{e}
		case "collab_tool_call":
			return []Event{{Type: "tool", ID: str(it["id"]), Name: "agents." + str(it["tool"]),
				Input: firstLine(str(it["prompt"])), Kind: KindOther, Status: status}}
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

// firstItemID is the first "id" of a Codex line's item, where a decoded
// map would keep the last of a repeated key.
func firstItemID(line []byte) string {
	var m struct {
		Item json.RawMessage `json:"item"`
	}
	if json.Unmarshal(line, &m) != nil {
		return ""
	}
	dec := json.NewDecoder(bytes.NewReader(m.Item))
	if t, err := dec.Token(); err != nil || t != json.Delim('{') {
		return ""
	}
	for dec.More() {
		key, err := dec.Token()
		if err != nil {
			return ""
		}
		var v json.RawMessage
		if dec.Decode(&v) != nil {
			return ""
		}
		if key == "id" {
			var id string
			_ = json.Unmarshal(v, &id)
			return id
		}
	}
	return ""
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
		name, args, output, failed := cursorTool(obj(m["tool_call"]))
		e := Event{Type: "tool", ID: str(m["call_id"]), Name: name, Status: "running"}
		if args != nil {
			e.Input = toolInput(args)
		}
		e.Kind, e.Path = cursorKind(name, obj(args))
		if e.Kind == KindSkill {
			e.Input = skillFile(e.Path)
		}
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

// cursorKind is what one of Cursor Agent's tools does, and the file it is
// about. It opens a skill by reading its SKILL.md.
func cursorKind(name string, args map[string]any) (kind, path string) {
	switch name {
	case "read":
		if path = str(args["path"]); skillFile(path) != "" {
			return KindSkill, path
		}
		return KindRead, path
	case "edit", "write", "delete":
		return KindWrite, str(args["path"])
	case "shell":
		return KindShell, ""
	case "webSearch":
		return KindSearch, ""
	case "webFetch", "fetch":
		return KindFetch, ""
	case "task":
		return KindAgent, ""
	}
	return KindOther, ""
}

// cursorTool unwraps {"<kind>ToolCall": {"args": …, "result": …}}.
func cursorTool(tc map[string]any) (name string, args any, output string, failed bool) {
	for k, v := range tc {
		if !strings.HasSuffix(k, "ToolCall") {
			continue
		}
		name = strings.TrimSuffix(k, "ToolCall")
		body := obj(v)
		args = body["args"]
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
	return "tool", nil, "", false
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
		e := Event{Type: "tool", ID: str(m["tool_id"]), Name: str(m["tool_name"]),
			Input: toolInput(m["parameters"]), Status: "running"}
		params := obj(m["parameters"])
		e.Kind, e.Path = geminiKind(e.Name, params)
		if e.Kind == KindSkill {
			e.Input = str(params["name"])
		}
		return []Event{e}
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

// geminiKind is what one of Gemini CLI's tools does, and the file it is
// about; the names and parameters are the CLI's own (activate_skill opens
// a skill by name).
func geminiKind(name string, params map[string]any) (kind, path string) {
	switch name {
	case "read_file":
		return KindRead, str(params["file_path"])
	case "read_many_files":
		return KindRead, ""
	case "write_file", "replace":
		return KindWrite, str(params["file_path"])
	case "run_shell_command":
		return KindShell, ""
	case "google_web_search":
		return KindSearch, ""
	case "web_fetch":
		return KindFetch, ""
	case "activate_skill":
		return KindSkill, ""
	case "invoke_agent", "codebase_investigator":
		return KindAgent, ""
	}
	return KindOther, ""
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
		if len(rest) >= 2 && rest[0] == '"' && rest[len(rest)-1] == '"' {
			// A command with single quotes in it comes double-quoted.
			return strings.NewReplacer(`\"`, `"`, `\\`, `\`, `\$`, `$`, "\\`", "`").Replace(rest[1 : len(rest)-1])
		}
		return rest
	}
	return cmd
}
