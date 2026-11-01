package codingagents

import "strings"

// ---- OpenCode (opencode run --format json) ---------------------------------

// OpenCode's turn is its session's parts, one per line, each with the
// session id: step_start and step_finish around each model call (the
// finish with that call's tokens), text, tool_use (a tool call once it has
// finished, with its input and output) and error (the turn failed).
type opencodeParser struct {
	session              string
	in, cached, out, ctx int64
}

func (p *opencodeParser) Parse(line []byte) []Event {
	m := decode(line)
	if m == nil {
		return nil
	}
	var out []Event
	if sid := str(m["sessionID"]); sid != "" && p.session == "" {
		p.session = sid
		out = append(out, Event{Type: "session", SessionID: sid})
	}
	part := obj(m["part"])
	switch str(m["type"]) {
	case "text":
		if t := str(part["text"]); strings.TrimSpace(t) != "" {
			out = append(out, Event{Type: "text", Text: t})
		}
	case "reasoning":
		if t := str(part["text"]); strings.TrimSpace(t) != "" {
			out = append(out, Event{Type: "thinking", Text: t})
		}
	case "tool_use":
		state := obj(part["state"])
		in := obj(state["input"])
		e := Event{Type: "tool", ID: firstNonEmpty(str(part["callID"]), str(part["id"])), Name: str(part["tool"]),
			Input: toolInput(in), Status: "running"}
		e.Kind, e.Path = opencodeKind(e.Name, in)
		switch str(state["status"]) {
		case "completed":
			e.Status, e.Output = "done", capText(str(state["output"]))
		case "error":
			e.Status, e.Output = "error", capText(firstNonEmpty(str(state["error"]), str(state["output"])))
		}
		out = append(out, e)
	case "step_finish":
		// Each model call's tokens: the turn is their sum, and the last call
		// what the conversation now fills. The turn's end is this, until a
		// later step or an error says more.
		tok := obj(part["tokens"])
		cache := obj(tok["cache"])
		read := num(cache["read"])
		in := num(tok["input"]) + read + num(cache["write"])
		p.in, p.cached, p.out = p.in+in, p.cached+read, p.out+num(tok["output"])
		p.ctx = in + num(tok["output"])
		e := Event{Type: "done", InputTokens: p.in, CachedTokens: p.cached, OutputTokens: p.out, ContextTokens: p.ctx}
		if c, ok := part["cost"].(float64); ok && c > 0 {
			e.CostUSD = c
		}
		out = append(out, e)
	case "error":
		errObj := obj(m["error"])
		msg := firstNonEmpty(str(obj(errObj["data"])["message"]), str(errObj["message"]), str(errObj["name"]), "The turn failed")
		out = append(out, Event{Type: "done", Error: msg, InputTokens: p.in, CachedTokens: p.cached, OutputTokens: p.out})
	}
	return out
}

// opencodeKind is what one of OpenCode's tools does, and the file it is
// about; the names and parameters are the CLI's own.
func opencodeKind(name string, in map[string]any) (kind, path string) {
	switch name {
	case "read":
		return KindRead, str(in["filePath"])
	case "write", "edit", "multiedit", "patch", "apply_patch":
		return KindWrite, str(in["filePath"])
	case "bash":
		return KindShell, ""
	case "websearch", "codesearch":
		return KindSearch, ""
	case "webfetch":
		return KindFetch, ""
	case "skill":
		return KindSkill, ""
	case "task":
		return KindAgent, ""
	}
	return KindOther, ""
}
