package pantheoncli

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync"
	"testing"
	"time"
)

// fakeAgent is the Hub, the controller and an Agent App in one server: each
// method answers from its handler, and the calls are kept.
type fakeAgent struct {
	server  *httptest.Server
	mu      sync.Mutex
	calls   []string
	args    map[string]map[string]any
	methods map[string]func(args map[string]any) any
}

func newFakeAgent(t *testing.T) *fakeAgent {
	f := &fakeAgent{args: map[string]map[string]any{}, methods: map[string]func(map[string]any) any{}}
	mux := http.NewServeMux()
	mux.HandleFunc("GET /api/fleet/apps/workload-identity", func(w http.ResponseWriter, r *http.Request) {
		json.NewEncoder(w).Encode(map[string]any{"fleet_id": "f_test", "controller_url": f.server.URL})
	})
	mux.HandleFunc("GET /deployments/general-team", func(w http.ResponseWriter, r *http.Request) {
		json.NewEncoder(w).Encode(map[string]any{"revision": 3, "status": map[string]any{"apps": map[string]any{
			"agent": map[string]any{"state": "ready", "node_id": "n_brain", "instance_id": "agent",
				"revision": strings.Repeat("a", 64), "generation": 2}}}})
	})
	mux.HandleFunc("POST /api/fleet/apps/workload-connect", func(w http.ResponseWriter, r *http.Request) {
		json.NewEncoder(w).Encode(map[string]any{"origin": f.server.URL, "access_token": "grant-agent",
			"expires": time.Now().Add(time.Hour).Unix()})
	})
	mux.HandleFunc("POST /rpc", func(w http.ResponseWriter, r *http.Request) {
		var call struct {
			Method string         `json:"method"`
			Args   map[string]any `json:"args"`
		}
		json.NewDecoder(r.Body).Decode(&call)
		f.mu.Lock()
		f.calls = append(f.calls, call.Method)
		f.args[call.Method] = call.Args
		h := f.methods[call.Method]
		f.mu.Unlock()
		var result any = map[string]any{"success": true}
		if h != nil {
			result = h(call.Args)
		}
		json.NewEncoder(w).Encode(map[string]any{"success": true, "result": result})
	})
	f.server = httptest.NewTLSServer(mux)
	t.Cleanup(f.server.Close)
	testTransport = f.server.Client().Transport
	t.Cleanup(func() { testTransport = nil })
	t.Setenv("PANTHEON_CLI_HOME", t.TempDir())
	t.Setenv("PANTHEON_KEY", "pbk_test")
	t.Setenv("PANTHEON_HUB_URL", f.server.URL)
	return f
}

func (f *fakeAgent) handle(method string, h func(args map[string]any) any) {
	f.mu.Lock()
	defer f.mu.Unlock()
	f.methods[method] = h
}

func (f *fakeAgent) called() []string {
	f.mu.Lock()
	defer f.mu.Unlock()
	return append([]string(nil), f.calls...)
}

// stream serves events as the Agent's event store does: each event's JSON
// in fragments, a page at a time, after the turn was sent.
func (f *fakeAgent) stream(events []map[string]any, sent <-chan struct{}) {
	var items []map[string]any
	for i, ev := range events {
		raw, _ := json.Marshal(ev)
		var parts []string
		for len(raw) > 0 {
			n := min(30, len(raw))
			parts = append(parts, string(raw[:n]))
			raw = raw[n:]
		}
		for _, p := range parts {
			items = append(items, map[string]any{"event_id": fmt.Sprint("e", i), "total": len(parts), "json_fragment": p})
		}
	}
	f.handle("read_agent_events", func(args map[string]any) any {
		select {
		case <-sent:
		default:
			return map[string]any{"events": []any{}, "cursor": args["cursor"], "has_more": false}
		}
		at := int(args["cursor"].(map[string]any)["sequence"].(float64))
		end := min(at+5, len(items))
		return map[string]any{"events": items[at:end], "cursor": map[string]any{"epoch": "x", "sequence": end},
			"has_more": end < len(items)}
	})
}

func turnLines(t *testing.T, out string) []TurnLine {
	t.Helper()
	var lines []TurnLine
	for _, l := range strings.Split(strings.TrimSpace(out), "\n") {
		var tl TurnLine
		if err := json.Unmarshal([]byte(l), &tl); err != nil {
			t.Fatalf("%q: %v", l, err)
		}
		lines = append(lines, tl)
	}
	return lines
}

func step(m map[string]any) map[string]any {
	return map[string]any{"type": "step", "data": map[string]any{"type": "step_message", "step_message": m}}
}

func runWithStdin(stdin string, args ...string) (string, string, int) {
	var out, errOut bytes.Buffer
	code := Main(args, strings.NewReader(stdin), &out, &errOut)
	return out.String(), errOut.String(), code
}

// A new conversation on a node: created with the model, put on the node and
// folder, the message sent, and its steps turned into lines until it ends.
func TestAgentTurnStreamsOneTurnAsJSONLines(t *testing.T) {
	f := newFakeAgent(t)
	sent := make(chan struct{})
	f.handle("create_chat", func(map[string]any) any { return map[string]any{"chat_id": "c1"} })
	f.handle("get_agent_event_cursor", func(map[string]any) any {
		return map[string]any{"protocol": 1, "chat_id": "c1", "cursor": map[string]any{"epoch": "x", "sequence": 0}}
	})
	f.handle("chat", func(map[string]any) any {
		close(sent)
		time.Sleep(300 * time.Millisecond)
		return map[string]any{"chat_id": "c1", "response": "Done."}
	})
	f.stream([]map[string]any{
		{"type": "chunk", "data": map[string]any{"chunk": map[string]any{"content": "Rea"}}},
		step(map[string]any{"role": "assistant", "content": "Reading.", "model": "acme/star-1",
			"usage":     map[string]any{"prompt_tokens": 100, "completion_tokens": 10, "prompt_tokens_details": map[string]any{"cached_tokens": 40}},
			"_metadata": map[string]any{"max_tokens": 1000},
			"tool_calls": []any{map[string]any{"id": "t1", "function": map[string]any{"name": "file_manager__read_file",
				"arguments": `{"file_path":"/work/acme/README.md"}`}}}}),
		step(map[string]any{"role": "tool", "tool_call_id": "t1", "tool_name": "file_manager__read_file",
			"content": `{"success": false, "error": "no such file"}`}),
		step(map[string]any{"role": "assistant", "id": "t2:1:call", "parent_tool_call_id": "t2",
			"execution_context_id": "c1|cli|t2", "tool_calls": []any{map[string]any{"id": "t2:1",
				"function": map[string]any{"name": "web__duckduckgo_search", "arguments": map[string]any{"query": "Sirius"}}}}}),
		step(map[string]any{"role": "assistant", "content": "It is missing.", "model": "acme/star-1",
			"usage": map[string]any{"prompt_tokens": 200, "completion_tokens": 5}}),
		{"type": "chat_finished", "data": map[string]any{"type": "chat_finished"}},
	}, sent)

	out, errOut, code := runWithStdin("Read README.md", "agent", "chat", "--json", "--node", "n_mac", "--workdir", "/work/acme",
		"--model", "low")
	if code != 0 {
		t.Fatalf("%d %s %s", code, out, errOut)
	}
	got := turnLines(t, out)
	want := []TurnLine{
		{Type: "chat", ChatID: "c1"},
		{Type: "text", Text: "Reading."},
		{Type: "tool_call", ID: "t1", Name: "file_manager__read_file", Args: json.RawMessage(`{"file_path":"/work/acme/README.md"}`)},
		{Type: "tool_result", ID: "t1", Name: "file_manager__read_file", Output: `{"success": false, "error": "no such file"}`, IsError: true},
		{Type: "tool_call", ID: "t2:1", Name: "web__duckduckgo_search", Args: json.RawMessage(`{"query":"Sirius"}`), Parent: "t2",
			Context: "c1|cli|t2"},
		{Type: "text", Text: "It is missing."},
		{Type: "done", ChatID: "c1", Status: "finished", Model: "acme/star-1",
			Usage: &TurnUsage{InputTokens: 300, CachedTokens: 40, OutputTokens: 15, ContextTokens: 205, ContextWindow: 1000}},
	}
	if a, b := mustJSON(got), mustJSON(want); a != b {
		t.Fatalf("lines\n got %s\nwant %s", a, b)
	}
	if m := f.args["create_chat"]["model"]; m != "low" {
		t.Errorf("create_chat %v", f.args["create_chat"])
	}
	if a := f.args["set_chat_node"]; a["chat_id"] != "c1" || a["node_id"] != "n_mac" || a["workdir"] != "/work/acme" {
		t.Errorf("set_chat_node %v", a)
	}
	msg := f.args["chat"]["message"].([]any)[0].(map[string]any)
	if msg["role"] != "user" || msg["content"] != "Read README.md" {
		t.Errorf("chat %v", f.args["chat"])
	}
}

func mustJSON(v any) string {
	b, _ := json.Marshal(v)
	return string(b)
}

// A conversation continued: its model is chosen for its agent; a turn the
// Agent refuses ends with its reason.
func TestAgentTurnContinuesAChat(t *testing.T) {
	f := newFakeAgent(t)
	f.handle("get_agents", func(map[string]any) any {
		return map[string]any{"agents": []any{map[string]any{"name": "Leader"}}}
	})
	f.handle("get_agent_event_cursor", func(map[string]any) any { return map[string]any{"cursor": nil} })
	f.handle("chat", func(map[string]any) any { return map[string]any{"success": false, "message": "Chat 'c9' not found"} })
	out, _, code := run(t, "agent", "chat", "--json", "--chat", "c9", "--model", "high", "--", "go", "on")
	if code != 0 {
		t.Fatalf("%d %s", code, out)
	}
	got := turnLines(t, out)
	if len(got) != 2 || mustJSON(got[0]) != mustJSON(TurnLine{Type: "chat", ChatID: "c9"}) ||
		mustJSON(got[1]) != mustJSON(TurnLine{Type: "done", ChatID: "c9", Status: "error", Error: "Chat 'c9' not found"}) {
		t.Fatalf("lines %s", out)
	}
	if a := f.args["set_agent_model"]; a["agent_name"] != "Leader" || a["model"] != "high" || a["chat_id"] != "c9" {
		t.Errorf("set_agent_model %v", a)
	}
	for _, m := range f.called() {
		if m == "create_chat" || m == "set_chat_node" {
			t.Errorf("%s was called", m)
		}
	}
	if msg := f.args["chat"]["message"].([]any)[0].(map[string]any); msg["content"] != "go on" {
		t.Errorf("message %v", msg)
	}
}

// Before the turn starts, a failure is one error line and exit 1.
func TestAgentTurnThatCannotStart(t *testing.T) {
	f := newFakeAgent(t)
	f.handle("set_chat_node", func(map[string]any) any {
		return map[string]any{"success": false, "error": "That node is not one of your nodes."}
	})
	f.handle("create_chat", func(map[string]any) any { return map[string]any{"chat_id": "c1"} })
	out, _, code := runWithStdin("hi", "agent", "chat", "--json", "--node", "n_gone")
	got := turnLines(t, out)
	if code != 1 || len(got) != 2 || got[1].Type != "error" ||
		got[1].Error != "working on node n_gone: That node is not one of your nodes." {
		t.Fatalf("%d %s", code, out)
	}
	if out, _, code := runWithStdin("  ", "agent", "chat", "--json"); code != 1 || !strings.Contains(out, `"type":"error"`) {
		t.Fatalf("an empty message: %d %s", code, out)
	}
}

// Stopping the command stops the Agent's turn.
func TestAgentTurnStopsTheAgentWhenStopped(t *testing.T) {
	f := newFakeAgent(t)
	f.handle("create_chat", func(map[string]any) any { return map[string]any{"chat_id": "c1"} })
	f.handle("chat", func(map[string]any) any {
		time.Sleep(2 * time.Second)
		return map[string]any{"chat_id": "c1", "response": ""}
	})
	creds, err := LoadCredentials()
	if err != nil {
		t.Fatal(err)
	}
	s, err := NewSession(creds)
	if err != nil {
		t.Fatal(err)
	}
	p, _ := parse([]string{"chat", "--json"}, nil)
	ctx, cancel := context.WithCancel(context.Background())
	var out bytes.Buffer
	r := &runner{ctx: ctx, s: s, p: p, json: true, stdin: strings.NewReader("hi"), stdout: &out, stderr: &bytes.Buffer{}}
	time.AfterFunc(500*time.Millisecond, cancel)
	if err := r.agentTurn(); err != nil {
		t.Fatal(err)
	}
	got := turnLines(t, out.String())
	if last := got[len(got)-1]; last.Type != "done" || last.Status != "stopped" {
		t.Fatalf("lines %s", out.String())
	}
	stopped := false
	for _, m := range f.called() {
		stopped = stopped || m == "stop_chat"
	}
	if !stopped {
		t.Fatalf("the Agent was not stopped: %v", f.called())
	}
}
