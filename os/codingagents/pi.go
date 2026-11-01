package codingagents

import "strings"

// ---- Pi (pi -p --mode json) ------------------------------------------------

// Pi's turn is its session's events, one per line (its docs, json.md): the
// session header first (its id), then agent_start, turn_start/turn_end
// around each model call, message_start/update/end for each message (an
// assistant message's content is text, thinking and toolCall parts; its
// usage, stop reason and error come with message_end),
// tool_execution_start/update/end for each tool, and agent_end. The text
// streams as message_update deltas; the steps are read from message_end.
type piParser struct {
	in, cached, out, ctx int64
	errText              string
	model                string
}

func (p *piParser) Parse(line []byte) []Event {
	m := decode(line)
	if m == nil {
		return nil
	}
	switch str(m["type"]) {
	case "session":
		return []Event{{Type: "session", SessionID: str(m["id"])}}
	case "message_end":
		msg := obj(m["message"])
		if str(msg["role"]) != "assistant" {
			return nil
		}
		var out []Event
		if p.model == "" && str(msg["model"]) != "" {
			p.model = str(msg["model"])
		}
		content, _ := msg["content"].([]any)
		for _, c := range content {
			part := obj(c)
			switch str(part["type"]) {
			case "text":
				if t := str(part["text"]); strings.TrimSpace(t) != "" {
					out = append(out, Event{Type: "text", Text: t})
				}
			case "thinking":
				if t := str(part["thinking"]); strings.TrimSpace(t) != "" {
					out = append(out, Event{Type: "thinking", Text: t})
				}
			case "toolCall":
				args := obj(part["arguments"])
				e := Event{Type: "tool", ID: str(part["id"]), Name: str(part["name"]), Input: toolInput(args), Status: "running"}
				e.Kind, e.Path = piKind(e.Name, args)
				out = append(out, e)
			}
		}
		u := obj(msg["usage"])
		read := num(u["cacheRead"])
		in := num(u["input"]) + read + num(u["cacheWrite"])
		p.in, p.cached, p.out = p.in+in, p.cached+read, p.out+num(u["output"])
		if in > 0 {
			p.ctx = in + num(u["output"])
		}
		if str(msg["stopReason"]) == "error" || str(msg["stopReason"]) == "aborted" {
			p.errText = firstNonEmpty(str(msg["errorMessage"]), "The turn failed")
		}
		return out
	case "tool_execution_end":
		e := Event{Type: "tool", ID: str(m["toolCallId"]), Status: "done", Output: capText(contentText(obj(m["result"])["content"]))}
		if m["isError"] == true {
			e.Status = "error"
		}
		return []Event{e}
	case "agent_end":
		return []Event{p.done()}
	case "auto_retry_start":
		if msg := str(m["errorMessage"]); msg != "" {
			return []Event{{Type: "text", Text: "⚠ " + firstLine(msg) + " (retrying)"}}
		}
	}
	return nil
}

func (p *piParser) done() Event {
	return Event{Type: "done", Model: p.model, Error: p.errText, InputTokens: p.in, CachedTokens: p.cached,
		OutputTokens: p.out, ContextTokens: p.ctx}
}

// piKind is what one of Pi's built-in tools does, and the file it is about.
func piKind(name string, args map[string]any) (kind, path string) {
	switch name {
	case "read":
		path = firstNonEmpty(str(args["path"]), str(args["file_path"]))
		if skillFile(path) != "" {
			return KindSkill, path
		}
		return KindRead, path
	case "write", "edit":
		return KindWrite, firstNonEmpty(str(args["path"]), str(args["file_path"]))
	case "bash":
		if name, path := shellSkill(str(args["command"])); name != "" {
			return KindSkill, path
		}
		return KindShell, ""
	}
	return KindOther, ""
}
