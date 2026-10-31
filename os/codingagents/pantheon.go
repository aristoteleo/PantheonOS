package codingagents

import (
	"strings"
)

// ---- Pantheon Agent (pantheon agent chat --json) ---------------------------

// The Pantheon Agent's turn as the pantheon command reports it
// (docs/os/pantheon-cli.md, "One turn as JSON lines"): chat, text,
// tool_call, tool_result, done, or one error line when the turn could not
// start. Its tools are App tools (file_manager__read_file,
// shell__run_command, …); the rest of PantheonOS it reaches through the
// pantheon command in run_command, whose own calls come as calls inside that
// one (parent). A sub-agent is `pantheon agent run` in run_command: the
// command is the launch, and the sub-agent's steps come inside it.
type pantheonParser struct {
	launches map[string]string // launch tool id -> sub-agent profile
	owner    map[string]string // tool id -> the launch it ran under
}

func (p *pantheonParser) Parse(line []byte) []Event {
	m := decode(line)
	if m == nil {
		return nil
	}
	if p.launches == nil {
		p.launches, p.owner = map[string]string{}, map[string]string{}
	}
	parent := p.launchOf(str(m["parent"]))
	switch str(m["type"]) {
	case "chat":
		return []Event{{Type: "session", SessionID: str(m["chat_id"])}}
	case "text":
		if t := str(m["text"]); strings.TrimSpace(t) != "" {
			return []Event{{Type: "text", Text: t, Parent: parent}}
		}
	case "tool_call":
		id, args := str(m["id"]), obj(m["args"])
		p.owner[id] = parent
		e := Event{Type: "tool", ID: id, Name: str(m["name"]), Status: "running", Parent: parent}
		in := map[string]any{}
		for k, v := range args {
			if k != "node" && k != "_background" {
				in[k] = v
			}
		}
		e.Input = toolInput(in)
		e.Kind, e.Path = pantheonKind(e.Name, args)
		switch e.Kind {
		case KindSkill:
			if e.Name == "skills__skill_view" {
				e.Input = str(args["name"])
			} else if name, path := shellSkill(str(args["command"])); name != "" {
				e.Input, e.Path = name, path
			} else {
				e.Input = skillFile(e.Path)
			}
		case KindAgent:
			profile, task := agentRun(str(args["command"]))
			e.Subagent, e.Text = profile, capText(task)
			e.Input = firstLine(firstNonEmpty(task, profile))
			p.launches[id] = profile
		}
		return []Event{e}
	case "tool_result":
		id := str(m["id"])
		output, failed := commandOutput(str(m["output"]))
		e := Event{Type: "tool", ID: id, Status: "done", Output: capText(output), Parent: p.owner[id]}
		if failed || m["is_error"] == true {
			e.Status = "error"
		}
		if profile, ok := p.launches[id]; ok {
			e.Subagent, e.Output = profile, capAt(subagentAnswer(output), reportCap)
			if e.Status == "error" {
				e.Error = firstLine(firstNonEmpty(e.Output, "The sub-agent failed"))
			}
		}
		return []Event{e}
	case "done":
		u := obj(m["usage"])
		e := Event{Type: "done", Model: str(m["model"]), InputTokens: num(u["input_tokens"]),
			CachedTokens: num(u["cached_tokens"]), OutputTokens: num(u["output_tokens"]),
			ContextTokens: num(u["context_tokens"]), ContextWindow: num(u["context_window"])}
		if str(m["status"]) == "error" {
			e.Error = firstNonEmpty(str(m["error"]), "The turn failed")
		}
		return []Event{e}
	case "error":
		return []Event{{Type: "done", Error: "Could not start Pantheon Agent: " +
			firstNonEmpty(str(m["error"]), "the pantheon command failed")}}
	}
	return nil
}

// launchOf is the sub-agent launch a call inside id belongs to: id itself
// when it is one, else the launch id ran under; "" for the main agent (a
// pantheon command's own calls are the agent's).
func (p *pantheonParser) launchOf(id string) string {
	if id == "" {
		return ""
	}
	if _, ok := p.launches[id]; ok {
		return id
	}
	return p.owner[id]
}

// pantheonKind is what one of the Pantheon Agent's tools does, and the file
// it is about. Tools are named app__tool.
func pantheonKind(name string, args map[string]any) (kind, path string) {
	app, tool, _ := strings.Cut(name, "__")
	switch app {
	case "file_manager":
		switch tool {
		case "read_file", "view_file_outline":
			if path = str(args["file_path"]); skillFile(path) != "" {
				return KindSkill, path
			}
			return KindRead, path
		case "read_pdf":
			return KindRead, str(args["pdf_path"])
		case "observe_images":
			if paths, _ := args["image_paths"].([]any); len(paths) > 0 {
				path = str(paths[0])
			}
			return KindRead, path
		case "write_file", "update_file", "apply_patch":
			return KindWrite, str(args["file_path"])
		case "move_file":
			return KindWrite, str(args["new_path"])
		case "delete_path":
			if path = str(args["path"]); path == "" {
				if paths, _ := args["path"].([]any); len(paths) > 0 {
					path = str(paths[0])
				}
			}
			return KindWrite, path
		}
	case "shell":
		if tool == "run_command" {
			cmd := str(args["command"])
			if profile, _ := agentRun(cmd); profile != "" {
				return KindAgent, ""
			}
			if name, _ := shellSkill(cmd); name != "" {
				return KindSkill, ""
			}
			return KindShell, ""
		}
	case "fleet":
		if tool == "run_on_node" {
			return KindShell, ""
		}
	case "web":
		switch {
		case strings.Contains(tool, "search"):
			return KindSearch, ""
		case strings.Contains(tool, "crawl") || strings.Contains(tool, "fetch"):
			return KindFetch, ""
		}
	case "skills":
		if tool == "skill_view" {
			return KindSkill, ""
		}
	}
	return KindOther, ""
}

// agentRun reads a `pantheon agent run` in a shell line: the profile it
// runs (the team, or a resumed run's profile) and the task; "" when the line
// runs no sub-agent.
func agentRun(cmd string) (profile, task string) {
	for _, seg := range shellCommands(cmd) {
		w := seg.words
		if len(w) < 3 || w[0] != "pantheon" || w[1] != "agent" || w[2] != "run" {
			continue
		}
		var words []string
		for i := 3; i < len(w); i++ {
			switch w[i] {
			case "--profile", "--team", "--resume", "--timeout":
				if i+1 < len(w) {
					if w[i] == "--resume" {
						// A run id is <conversation>.d1.<profile>.<id>.
						if parts := strings.Split(w[i+1], "."); len(parts) >= 4 {
							profile = firstNonEmpty(profile, parts[len(parts)-2])
						}
					} else if w[i] != "--timeout" {
						profile = w[i+1]
					}
					i++
				}
			default:
				if !strings.HasPrefix(w[i], "--") {
					words = append(words, w[i])
				}
			}
		}
		if profile == "" && len(words) > 0 {
			profile, words = words[0], words[1:]
		}
		return firstNonEmpty(profile, "agent"), strings.Join(words, " ")
	}
	return "", ""
}

// commandOutput is a tool result as a person reads it: a command's output
// or a file's content rather than the JSON around it, and whether it failed.
func commandOutput(output string) (string, bool) {
	r := decode([]byte(output))
	if r == nil {
		return output, false
	}
	failed := r["success"] == false
	if code, ok := r["exit_code"].(float64); ok && code != 0 {
		failed = true
	}
	for _, k := range []string{"output", "content"} {
		if out, ok := r[k].(string); ok {
			return out, failed
		}
	}
	if e := str(r["error"]); e != "" {
		return e, true
	}
	return output, failed
}

// subagentAnswer is a sub-agent's answer without the note the command adds
// after it (the run id, and how to continue it).
func subagentAnswer(output string) string {
	if i := strings.LastIndex(output, "\n(run "); i >= 0 && strings.Contains(output[i:], "pantheon agent run --resume") {
		output = output[:i]
	}
	return strings.TrimSpace(output)
}
