package pantheoncli

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"os"
	"os/signal"
	"strings"
	"syscall"
	"time"
)

// `pantheon agent chat --json` is one turn of the owner's Agent without the
// terminal: the message in (after --, after -i, or on stdin), the turn out as
// JSON lines (docs/os/pantheon-cli.md, "One turn as JSON lines"). The
// coding-agents service runs it for the Agent Canvas, like the other agents'
// headless modes. It speaks to the Agent App directly: nothing to fetch or
// install first.
//
// Lines, in order:
//
//	{"type":"chat","chat_id":"…"}                      the conversation, first
//	{"type":"text","text":"…"}                         the Agent's text
//	{"type":"tool_call","id":"…","name":"…","args":{…}} a tool call
//	{"type":"tool_result","id":"…","name":"…","output":"…","is_error":true}
//	{"type":"done","chat_id":"…","status":"finished|error|stopped","error":"…",
//	 "model":"…","usage":{"input_tokens":…,"cached_tokens":…,"output_tokens":…,
//	 "context_tokens":…,"context_window":…}}
//
// A call made inside another one (a pantheon command the Agent ran: an App
// tool, a sub-agent's steps) carries "parent", the outer call's id, and
// "context", the execution context it ran in. Before the turn starts, a
// failure is one line {"type":"error","error":"…"} and exit 1.

// TurnLine is one line of `pantheon agent chat --json`.
type TurnLine struct {
	Type    string          `json:"type"`
	ChatID  string          `json:"chat_id,omitempty"`
	Text    string          `json:"text,omitempty"`
	ID      string          `json:"id,omitempty"`
	Name    string          `json:"name,omitempty"`
	Args    json.RawMessage `json:"args,omitempty"`
	Output  string          `json:"output,omitempty"`
	IsError bool            `json:"is_error,omitempty"`
	Parent  string          `json:"parent,omitempty"`
	Context string          `json:"context,omitempty"`
	Status  string          `json:"status,omitempty"`
	Error   string          `json:"error,omitempty"`
	Model   string          `json:"model,omitempty"`
	Usage   *TurnUsage      `json:"usage,omitempty"`
}

// TurnUsage is a turn's size: tokens read (cached included), of those served
// from cache, and written; the last request's tokens against the model's
// window.
type TurnUsage struct {
	InputTokens   int64 `json:"input_tokens"`
	CachedTokens  int64 `json:"cached_tokens"`
	OutputTokens  int64 `json:"output_tokens"`
	ContextTokens int64 `json:"context_tokens"`
	ContextWindow int64 `json:"context_window,omitempty"`
}

const (
	// turnOutputMax bounds a tool result's output on its line.
	turnOutputMax = 16 << 10
	// turnCallTimeout is how long the call that runs the turn is held; a
	// longer turn goes on, and its end comes from the event stream.
	turnCallTimeout = 600 * time.Second
	// turnIdleCheck is how long the stream may stay quiet, once that call
	// is gone without an answer, before the conversation is asked whether
	// it still runs.
	turnIdleCheck = 30 * time.Second
	turnPoll      = 250 * time.Millisecond
)

// turnMessage is the message of a --json turn.
func (r *runner) turnMessage() (string, error) {
	pos := r.p.positional
	var text string
	switch {
	case len(r.p.rest) > 0:
		text = strings.Join(r.p.rest, " ")
	case len(pos) >= 3 && (pos[1] == "-i" || pos[1] == "--message"):
		text = strings.Join(pos[2:], " ")
	case r.stdin != nil:
		data, err := io.ReadAll(io.LimitReader(r.stdin, 8<<20))
		if err != nil {
			return "", err
		}
		text = string(data)
	}
	if strings.TrimSpace(text) == "" {
		return "", errors.New("usage: pantheon agent chat --json [--chat ID] [--node N [--workdir DIR]] [--model M] -- MESSAGE (or the message on stdin)")
	}
	return text, nil
}

// failed is an App result's own failure (success false, with an error or a
// message).
func failed(result json.RawMessage) error {
	var r struct {
		Success *bool  `json:"success"`
		Error   any    `json:"error"`
		Message string `json:"message"`
	}
	if json.Unmarshal(result, &r) != nil || r.Success == nil || *r.Success {
		return nil
	}
	switch {
	case r.Error != nil:
		return errors.New(strings.TrimSpace(fmt.Sprint(r.Error)))
	case r.Message != "":
		return errors.New(r.Message)
	}
	return errors.New("the Agent reported a failure")
}

func (r *runner) agentTurn() error {
	enc := json.NewEncoder(r.stdout)
	enc.SetEscapeHTML(false)
	line := func(l TurnLine) { _ = enc.Encode(l) }
	err := r.runTurn(line)
	if err != nil {
		line(TurnLine{Type: "error", Error: err.Error()})
		return &exitCode{code: 1}
	}
	return nil
}

// runTurn sets the conversation up and follows its turn to the end. An
// error before the turn starts is returned; after, the turn ends with a done
// line.
func (r *runner) runTurn(line func(TurnLine)) error {
	message, err := r.turnMessage()
	if err != nil {
		return err
	}
	ctx, stop := signal.NotifyContext(r.ctx, os.Interrupt, syscall.SIGTERM)
	defer stop()
	dep, err := r.s.Deployment(ctx)
	if err != nil {
		return err
	}
	b, err := dep.AppBinding("agent")
	if err != nil {
		return err
	}
	call := func(method string, args map[string]any, timeout time.Duration) (json.RawMessage, error) {
		result, err := r.s.Call(ctx, b, method, args, timeout)
		if err == nil {
			err = failed(result)
		}
		return result, err
	}
	chatID, model := r.p.flags["chat"], r.p.flags["model"]
	if chatID == "" {
		args := map[string]any{}
		if model != "" {
			args["model"] = model
		}
		result, err := call("create_chat", args, 120*time.Second)
		if err != nil {
			return fmt.Errorf("creating the conversation: %w", err)
		}
		var created struct {
			ChatID string `json:"chat_id"`
		}
		if json.Unmarshal(result, &created) != nil || created.ChatID == "" {
			return errors.New("the Agent did not return the new conversation's id")
		}
		chatID = created.ChatID
	} else if model != "" {
		result, err := call("get_agents", map[string]any{"chat_id": chatID}, 60*time.Second)
		if err != nil {
			return fmt.Errorf("choosing the model: %w", err)
		}
		var team struct {
			Agents []struct {
				Name string `json:"name"`
			} `json:"agents"`
		}
		if json.Unmarshal(result, &team) != nil || len(team.Agents) == 0 || team.Agents[0].Name == "" {
			return errors.New("choosing the model: the conversation has no agent")
		}
		if _, err := call("set_agent_model", map[string]any{"chat_id": chatID, "agent_name": team.Agents[0].Name,
			"model": model}, 60*time.Second); err != nil {
			return fmt.Errorf("choosing the model: %w", err)
		}
	}
	line(TurnLine{Type: "chat", ChatID: chatID})
	if node := r.p.flags["node"]; node != "" {
		// Each turn: a conversation continued elsewhere works here now.
		if _, err := call("set_chat_node", map[string]any{"chat_id": chatID, "node_id": node,
			"workdir": r.p.flags["workdir"]}, 60*time.Second); err != nil {
			return fmt.Errorf("working on node %s: %w", node, err)
		}
	}
	result, err := call("get_agent_event_cursor", map[string]any{"chat_id": chatID}, 60*time.Second)
	if err != nil {
		return err
	}
	var position struct {
		Cursor json.RawMessage `json:"cursor"`
	}
	if json.Unmarshal(result, &position) != nil {
		return errors.New("the Agent returned an invalid event position")
	}
	t := &agentTurnState{r: r, b: b, chatID: chatID, cursor: position.Cursor, line: line}
	t.follow(ctx, message)
	return nil
}

type agentTurnState struct {
	r      *runner
	b      Binding
	chatID string
	cursor json.RawMessage
	line   func(TurnLine)
	// The event being put together from its fragments.
	pending   string
	fragments []string
	// What the turn's model requests added up to, and the last model.
	usage TurnUsage
	model string
}

type sent struct {
	result json.RawMessage
	err    error
}

// follow sends the message and turns the Agent's events into lines until
// the turn ends: its chat_finished event, or the end of the call that runs it.
func (t *agentTurnState) follow(ctx context.Context, message string) {
	callCtx, cancel := context.WithCancel(context.Background())
	defer cancel()
	done := make(chan sent, 1)
	go func() {
		args := map[string]any{"chat_id": t.chatID, "message": []map[string]any{{"role": "user", "content": message}}}
		result, err := t.r.s.Call(callCtx, t.b, "chat", args, turnCallTimeout)
		done <- sent{result, err}
	}()
	var answer *sent
	var gone bool // the call ended without an answer: the stream says the rest
	quiet := time.Now()
	for {
		if ctx.Err() != nil {
			t.stop()
			t.end("stopped", "")
			return
		}
		settled := answer != nil
		if !settled {
			select {
			case s := <-done:
				switch {
				case s.err == nil:
					if err := failed(s.result); err != nil {
						t.end("error", err.Error())
						return
					}
					var queued struct {
						Queued bool `json:"queued"`
					}
					if json.Unmarshal(s.result, &queued) == nil && queued.Queued {
						t.end("error", "This conversation is already running; the message was queued for it")
						return
					}
				case errors.As(s.err, new(*RPCError)):
					t.end("error", s.err.Error())
					return
				default:
					gone = true
				}
				answer = &s
			default:
			}
		}
		events, more, err := t.read(ctx)
		if err != nil {
			if ctx.Err() != nil {
				continue
			}
			t.end("error", "reading the Agent's events: "+err.Error())
			return
		}
		for _, ev := range events {
			if status, text, ok := t.translate(ev); ok {
				t.end(status, text)
				return
			}
		}
		if len(events) > 0 {
			quiet = time.Now()
		}
		// Once the call has answered, one more page is read: the turn's
		// last events may have raced its answer.
		if settled && !gone && !more {
			t.end("finished", "")
			return
		}
		if gone && time.Since(quiet) > turnIdleCheck {
			quiet = time.Now()
			if running, err := t.running(ctx); err == nil && !running {
				t.end("finished", "")
				return
			}
		}
		if !more {
			select {
			case <-ctx.Done():
			case <-time.After(turnPoll):
			}
		}
	}
}

func (t *agentTurnState) end(status, text string) {
	if status == "error" && text == "" {
		text = "The Agent's turn failed"
	}
	end := TurnLine{Type: "done", ChatID: t.chatID, Status: status, Error: text, Model: t.model}
	if t.usage != (TurnUsage{}) {
		end.Usage = &t.usage
	}
	t.line(end)
}

// stop asks the Agent to stop the turn: the user stopped this command.
func (t *agentTurnState) stop() {
	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()
	_, _ = t.r.s.Call(ctx, t.b, "stop_chat", map[string]any{"chat_id": t.chatID}, 10*time.Second)
}

// running asks whether the conversation still runs a turn.
func (t *agentTurnState) running(ctx context.Context) (bool, error) {
	result, err := t.r.s.Call(ctx, t.b, "list_chats", nil, 60*time.Second)
	if err != nil {
		return false, err
	}
	var list struct {
		Chats []struct {
			ID      string `json:"id"`
			Running bool   `json:"running"`
		} `json:"chats"`
	}
	if err := json.Unmarshal(result, &list); err != nil {
		return false, err
	}
	for _, c := range list.Chats {
		if c.ID == t.chatID {
			return c.Running, nil
		}
	}
	return false, nil
}

// agentEvent is one event of the Agent's stream.
type agentEvent struct {
	Type string          `json:"type"`
	Data json.RawMessage `json:"data"`
}

// read is the next page of the conversation's events, put together from
// their fragments. After a gap the stream reports, it goes on from now: the
// events in the gap are not shown.
func (t *agentTurnState) read(ctx context.Context) ([]agentEvent, bool, error) {
	result, err := t.r.s.Call(ctx, t.b, "read_agent_events", map[string]any{"chat_id": t.chatID, "cursor": t.cursor,
		"limit": 128}, 60*time.Second)
	if err != nil {
		return nil, false, err
	}
	var page struct {
		Events []struct {
			EventID  string `json:"event_id"`
			Total    int    `json:"total"`
			Fragment string `json:"json_fragment"`
		} `json:"events"`
		Cursor json.RawMessage `json:"cursor"`
		More   bool            `json:"has_more"`
		Reset  bool            `json:"reset_required"`
	}
	if err := json.Unmarshal(result, &page); err != nil {
		return nil, false, errors.New("the Agent returned an invalid event page")
	}
	if page.Reset {
		t.pending, t.fragments = "", nil
		result, err := t.r.s.Call(ctx, t.b, "get_agent_event_cursor", map[string]any{"chat_id": t.chatID}, 60*time.Second)
		if err != nil {
			return nil, false, err
		}
		var position struct {
			Cursor json.RawMessage `json:"cursor"`
		}
		if json.Unmarshal(result, &position) != nil {
			return nil, false, errors.New("the Agent returned an invalid event position")
		}
		t.cursor = position.Cursor
		return nil, true, nil
	}
	var out []agentEvent
	for _, item := range page.Events {
		if t.pending != item.EventID {
			t.pending, t.fragments = item.EventID, nil
		}
		t.fragments = append(t.fragments, item.Fragment)
		if len(t.fragments) < item.Total {
			continue
		}
		var ev agentEvent
		if json.Unmarshal([]byte(strings.Join(t.fragments, "")), &ev) == nil {
			out = append(out, ev)
		}
		t.pending, t.fragments = "", nil
	}
	if len(page.Cursor) > 0 && string(page.Cursor) != "null" {
		t.cursor = page.Cursor
	}
	return out, page.More, nil
}

// stepMessage is a message the Agent's turn added: its text and tool calls,
// or a tool's result.
type stepMessage struct {
	Role      string `json:"role"`
	Content   any    `json:"content"`
	ToolCalls []struct {
		ID       string `json:"id"`
		Function struct {
			Name      string `json:"name"`
			Arguments any    `json:"arguments"`
		} `json:"function"`
	} `json:"tool_calls"`
	ToolCallID string `json:"tool_call_id"`
	ToolName   string `json:"tool_name"`
	Parent     string `json:"parent_tool_call_id"`
	Context    string `json:"execution_context_id"`
	Model      string `json:"model"`
	Usage      struct {
		Prompt     int64 `json:"prompt_tokens"`
		Completion int64 `json:"completion_tokens"`
		Details    struct {
			Cached int64 `json:"cached_tokens"`
		} `json:"prompt_tokens_details"`
	} `json:"usage"`
	Metadata struct {
		MaxTokens int64 `json:"max_tokens"`
	} `json:"_metadata"`
}

// translate writes an event's lines; ok when it ends the turn (with its
// status and error).
func (t *agentTurnState) translate(ev agentEvent) (status, text string, ok bool) {
	switch ev.Type {
	case "chat_finished":
		var data struct {
			Status   string `json:"status"`
			Metadata struct {
				Message string `json:"message"`
			} `json:"metadata"`
		}
		_ = json.Unmarshal(ev.Data, &data)
		if data.Status == "error" {
			return "error", data.Metadata.Message, true
		}
		return "finished", "", true
	case "step":
		var data struct {
			Message stepMessage `json:"step_message"`
		}
		if json.Unmarshal(ev.Data, &data) != nil {
			return
		}
		m := data.Message
		parent, ctx := m.Parent, m.Context
		switch m.Role {
		case "assistant":
			if m.Model != "" && ctx == "" {
				// The main agent's request; a sub-agent's are its own.
				t.model = m.Model
				t.usage.InputTokens += m.Usage.Prompt
				t.usage.CachedTokens += m.Usage.Details.Cached
				t.usage.OutputTokens += m.Usage.Completion
				t.usage.ContextTokens = m.Usage.Prompt + m.Usage.Completion
				if m.Metadata.MaxTokens > 0 {
					t.usage.ContextWindow = m.Metadata.MaxTokens
				}
			}
			if s := contentText(m.Content); strings.TrimSpace(s) != "" {
				t.line(TurnLine{Type: "text", Text: s, Parent: parent, Context: ctx})
			}
			for _, c := range m.ToolCalls {
				t.line(TurnLine{Type: "tool_call", ID: c.ID, Name: c.Function.Name, Args: toolArgs(c.Function.Arguments),
					Parent: parent, Context: ctx})
			}
		case "tool":
			if m.ToolCallID == "" {
				return
			}
			out := contentText(m.Content)
			if len(out) > turnOutputMax {
				out = out[:turnOutputMax] + "…"
			}
			t.line(TurnLine{Type: "tool_result", ID: m.ToolCallID, Name: m.ToolName, Output: out,
				IsError: toolFailed(m.Content), Parent: parent, Context: ctx})
		}
	}
	return
}

// contentText is a message's content as text: a string, or its text parts.
func contentText(content any) string {
	switch c := content.(type) {
	case string:
		return c
	case []any:
		var parts []string
		for _, p := range c {
			if m, ok := p.(map[string]any); ok && m["type"] == "text" {
				if s, ok := m["text"].(string); ok {
					parts = append(parts, s)
				}
			}
		}
		return strings.Join(parts, "\n")
	case nil:
		return ""
	}
	b, _ := json.Marshal(content)
	return string(b)
}

// toolArgs is a call's arguments as JSON: the model's string, parsed when
// it is JSON.
func toolArgs(raw any) json.RawMessage {
	switch a := raw.(type) {
	case string:
		if json.Valid([]byte(a)) {
			return json.RawMessage(a)
		}
		b, _ := json.Marshal(map[string]any{"_unparsed_arguments": a})
		return b
	case nil:
		return json.RawMessage("{}")
	}
	b, _ := json.Marshal(raw)
	return b
}

// toolFailed reads a tool result that says it failed: a JSON object with
// success false or an error.
func toolFailed(content any) bool {
	s, ok := content.(string)
	if !ok {
		return false
	}
	var r struct {
		Success *bool `json:"success"`
		Error   any   `json:"error"`
	}
	if json.Unmarshal([]byte(s), &r) != nil {
		return false
	}
	if r.Success != nil {
		return !*r.Success
	}
	e, _ := r.Error.(string)
	return e != ""
}
