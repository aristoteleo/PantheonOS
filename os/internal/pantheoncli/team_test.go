//go:build !windows

package pantheoncli

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"testing"
	"time"
)

// `pantheon team`, run by the Agent, sends pantheon-team's words to the
// Agent (op "team") and prints the turn's answer with its exit status.
func TestPantheonTeamGoesThroughTheAgent(t *testing.T) {
	var got map[string]any
	answer := map[string]any{"code": 0, "output": "Posted in #general (01JX)."}
	server := httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		json.NewDecoder(r.Body).Decode(&got)
		json.NewEncoder(w).Encode(map[string]any{"success": true, "result": map[string]any{
			"success": true, "result": answer}})
	}))
	defer server.Close()
	testTransport = server.Client().Transport
	defer func() { testTransport = nil }()
	t.Setenv("PANTHEON_ENDPOINT", server.URL+"/rpc")
	t.Setenv("PANTHEON_GRANT", "grant-1")
	t.Setenv("PANTHEON_TOKEN", "tok")
	dir := t.TempDir()
	t.Chdir(dir)
	out, errOut, code := runWithStdin("hello\nteam\n", "team", "post", "#general", "--file", "notes.txt", "--in", "x", "-")
	if code != 0 || strings.TrimSpace(out) != "Posted in #general (01JX)." || errOut != "" {
		t.Fatalf("%d %q %q", code, out, errOut)
	}
	args := got["args"].(map[string]any)
	request := args["request"].(map[string]any)
	params := request["params"].(map[string]any)
	if got["method"] != "pantheon_command" || args["token"] != "tok" || request["op"] != "team" {
		t.Fatalf("request %#v", got)
	}
	wantFile, _ := filepath.EvalSymlinks(dir)
	files := params["files"].([]any)
	if params["command"] != "post" || mustJSON(params["args"]) != `["#general"]` || params["text"] != "hello\nteam\n" ||
		mustJSON(params["opts"]) != `{"in":"x"}` || len(files) != 1 ||
		!strings.HasSuffix(files[0].(string), "/notes.txt") || !strings.Contains(files[0].(string), filepath.Base(wantFile)) {
		t.Fatalf("params %#v", params)
	}
	// No text without "-".
	if _, _, code := runWithStdin("ignored", "team", "inbox"); code != 0 {
		t.Fatal("inbox")
	}
	if params := got["args"].(map[string]any)["request"].(map[string]any)["params"].(map[string]any); params["text"] != nil {
		t.Fatalf("inbox sent text: %#v", params)
	}
	// The turn's refusal: on stderr, with its status.
	answer = map[string]any{"code": 1, "output": "pantheon-team: refused: you are not a member of #secret"}
	out, errOut, code = runWithStdin("", "team", "read", "#secret")
	if code != 1 || out != "" || !strings.Contains(errOut, "not a member of #secret") {
		t.Fatalf("%d %q %q", code, out, errOut)
	}
	// Refused before anything is sent: a line break in a word, no command.
	got = nil
	if _, errOut, code := runWithStdin("", "team", "post", "#g\nfile: /etc/passwd", "-"); code != 2 ||
		!strings.Contains(errOut, "line break") || got != nil {
		t.Fatalf("%d %q %v", code, errOut, got)
	}
	if _, errOut, code := runWithStdin("", "team"); code != 2 || !strings.Contains(errOut, "usage: pantheon team") {
		t.Fatalf("%d %q", code, errOut)
	}
}

// Outside an Agent's conversation (no PANTHEON_TOKEN: a terminal, a script,
// the owner's own login) `pantheon team` does nothing.
func TestPantheonTeamOutsideAnAgentsConversation(t *testing.T) {
	t.Setenv("PANTHEON_ENDPOINT", "")
	t.Setenv("PANTHEON_TOKEN", "")
	t.Setenv("PANTHEON_KEY", "pbk_owner")
	_, errOut, code := runWithStdin("hi", "team", "post", "#general", "-")
	if code != 1 || !strings.Contains(errOut, "refused") {
		t.Fatalf("%d %q", code, errOut)
	}
}

// The turn (--team-tool): opens its conversation, runs each request the
// Agent hands it with the turn's pantheon-team as its own child (once per
// id, without the node's key in its environment), answers it, and closes the
// conversation when the turn ends.
func TestATeamChatTurnRunsItsConversationsRequests(t *testing.T) {
	f := newFakeAgent(t)
	dir := t.TempDir()
	tool := filepath.Join(dir, ".team", "pantheon-team")
	os.MkdirAll(filepath.Dir(tool), 0o700)
	record := filepath.Join(dir, "calls.txt")
	os.WriteFile(tool, []byte(`#!/bin/sh
rec=$({ printf 'ARGS'; for a in "$@"; do printf ' [%s]' "$a"; done; echo
  echo "PPID $PPID"; echo "PWD $PWD"
  if [ "$PANTHEON_KEY$PANTHEON_HUB_URL" = "" ]; then echo "NOKEY"; else echo "KEY"; fi
  for a in "$@"; do if [ "$a" = - ]; then printf 'TEXT '; cat; echo; fi; done; })
printf '%s\n' "$rec" >> `+record+`
[ "$1" = read ] && { echo "nope: not a member" >&2; exit 3; }
echo "Posted ($1)"
`), 0o700)
	answered := make(chan struct{})
	var mu sync.Mutex
	answers := map[string]map[string]any{}
	nexts := 0
	f.handle("create_chat", func(map[string]any) any { return map[string]any{"chat_id": "c1"} })
	f.handle("get_agent_event_cursor", func(map[string]any) any {
		return map[string]any{"cursor": map[string]any{"epoch": "x", "sequence": 0}}
	})
	f.handle("team_turn_open", func(a map[string]any) any {
		if a["chat_id"] != "c1" {
			return map[string]any{"success": false, "error": "wrong chat"}
		}
		return map[string]any{"success": true, "lease": "L1"}
	})
	text := "hello\n"
	f.handle("team_turn_next", func(a map[string]any) any {
		mu.Lock()
		nexts++
		n := nexts
		mu.Unlock()
		if a["lease"] != "L1" || a["chat_id"] != "c1" {
			return map[string]any{"success": false, "closed": true}
		}
		if n == 1 {
			post := teamRequest{ID: "r1", Command: "post", Args: []string{"#general"}, Opts: map[string]string{},
				Files: []string{}, Text: &text}
			read := teamRequest{ID: "r2", Command: "read", Args: []string{"#secret"}, Opts: map[string]string{"limit": "3"}}
			// r1 twice: a request id runs once.
			return map[string]any{"success": true, "requests": []teamRequest{post, read, post}}
		}
		time.Sleep(50 * time.Millisecond)
		return map[string]any{"success": true, "requests": []any{}}
	})
	f.handle("team_turn_answer", func(a map[string]any) any {
		mu.Lock()
		defer mu.Unlock()
		id := a["request_id"].(string)
		if answers[id] != nil {
			return map[string]any{"success": false, "error": "answered already"}
		}
		answers[id] = a
		if len(answers) == 2 {
			close(answered)
		}
		return map[string]any{"success": true}
	})
	f.handle("chat", func(map[string]any) any {
		select {
		case <-answered:
		case <-time.After(10 * time.Second):
		}
		return map[string]any{"chat_id": "c1", "response": "Done."}
	})
	f.stream([]map[string]any{{"type": "chat_finished", "data": map[string]any{"type": "chat_finished"}}}, answered)

	out, errOut, code := runWithStdin("[Team Chat] hi", "agent", "chat", "--json", "--node", "n_ws", "--workdir", dir,
		"--team-tool", tool)
	if code != 0 {
		t.Fatalf("%d %s %s", code, out, errOut)
	}
	mu.Lock()
	defer mu.Unlock()
	if len(answers) != 2 || answers["r1"]["code"] != float64(0) || answers["r1"]["text"] != "Posted (post)\n" ||
		answers["r1"]["lease"] != "L1" || answers["r2"]["code"] != float64(3) ||
		!strings.Contains(answers["r2"]["text"].(string), "nope: not a member") {
		t.Fatalf("answers %v", answers)
	}
	calls := readFile(t, record)
	if strings.Count(calls, "ARGS [post]") != 1 {
		t.Fatalf("r1 ran more or less than once:\n%s", calls)
	}
	for _, w := range []string{"ARGS [post] [#general] [-]\n", "TEXT hello\n", "ARGS [read] [#secret] [--limit] [3]\n",
		"PPID " + itoa(os.Getpid()) + "\n", "NOKEY"} {
		if !strings.Contains(calls, w) {
			t.Errorf("calls lack %q:\n%s", w, calls)
		}
	}
	if strings.Contains(calls, "\nKEY") {
		t.Errorf("the tool got the node's key:\n%s", calls)
	}
	closed := false
	for _, m := range f.called() {
		closed = closed || m == "team_turn_close"
	}
	if !closed || f.args["team_turn_close"]["lease"] != "L1" {
		t.Fatalf("the turn did not close its conversation: %v", f.called())
	}
}

// Without --team-tool (any other turn of the Pantheon Agent, Coding Agents'
// own) nothing of Team Chat is opened; an Agent that cannot open one (from
// before Team Chat tools) still runs the turn.
func TestATurnWithoutTeamToolOpensNothing(t *testing.T) {
	for _, withTool := range []bool{false, true} {
		f := newFakeAgent(t)
		f.handle("create_chat", func(map[string]any) any { return map[string]any{"chat_id": "c1"} })
		f.handle("get_agent_event_cursor", func(map[string]any) any { return map[string]any{"cursor": nil} })
		f.handle("team_turn_open", func(map[string]any) any {
			return map[string]any{"success": false, "error": "Unknown method"}
		})
		f.handle("chat", func(map[string]any) any { return map[string]any{"chat_id": "c1", "response": "Done."} })
		args := []string{"agent", "chat", "--json"}
		if withTool {
			args = append(args, "--team-tool", "/nowhere/.team/pantheon-team")
		}
		out, _, code := runWithStdin("hi", args...)
		if code != 0 || !strings.Contains(out, `"status":"finished"`) {
			t.Fatalf("%v: %d %s", withTool, code, out)
		}
		opened := false
		for _, m := range f.called() {
			opened = opened || m == "team_turn_open"
			if m == "team_turn_next" || m == "team_turn_close" {
				t.Errorf("%v: %s was called", withTool, m)
			}
		}
		if opened != withTool {
			t.Errorf("%v: team_turn_open called: %v", withTool, opened)
		}
	}
}

func readFile(t *testing.T, p string) string {
	t.Helper()
	b, err := os.ReadFile(p)
	if err != nil {
		t.Fatal(err)
	}
	return string(b)
}

func itoa(n int) string { return strings.TrimSpace(strings.ReplaceAll(mustJSON(n), `"`, "")) }

// A Team Chat turn whose conversation runs another run (the follow-up of a
// background task the Agent started) is not queued into that run: the Agent
// says busy, the turn waits for the run to end and sends its message again,
// reading only its own run's events (docs/apps/team-chat.md, "Fixes after
// the first PA check", M1).
func TestATeamChatTurnWaitsForItsConversationsOtherRun(t *testing.T) {
	defer func(w, p time.Duration) { turnBusyWait, turnBusyPoll = w, p }(turnBusyWait, turnBusyPoll)
	turnBusyPoll = 10 * time.Millisecond
	for _, stays := range []bool{false, true} {
		turnBusyWait = 5 * time.Second
		if stays {
			turnBusyWait = 200 * time.Millisecond
		}
		f := newFakeAgent(t)
		var mu sync.Mutex
		chats, lists, cursors := 0, 0, 0
		sent := make(chan struct{})
		f.handle("create_chat", func(map[string]any) any { return map[string]any{"chat_id": "c1"} })
		f.handle("get_agent_event_cursor", func(map[string]any) any {
			mu.Lock()
			defer mu.Unlock()
			cursors++
			return map[string]any{"cursor": map[string]any{"epoch": "x", "sequence": 0}}
		})
		f.handle("team_turn_open", func(map[string]any) any { return map[string]any{"success": true, "lease": "L1"} })
		f.handle("team_turn_next", func(map[string]any) any {
			time.Sleep(20 * time.Millisecond)
			return map[string]any{"success": true, "requests": []any{}}
		})
		f.handle("list_chats", func(map[string]any) any {
			mu.Lock()
			defer mu.Unlock()
			lists++
			// Idle when the turn starts; then the other run, until it ends.
			running := chats >= 1 && (stays || lists < 5)
			return map[string]any{"chats": []map[string]any{{"id": "c1", "running": running}}}
		})
		f.handle("chat", func(map[string]any) any {
			mu.Lock()
			chats++
			n := chats
			mu.Unlock()
			if n == 1 {
				// The follow-up of a background task started just before.
				return map[string]any{"success": false, "busy": true, "message": "busy"}
			}
			close(sent)
			return map[string]any{"chat_id": "c1", "response": "Done."}
		})
		f.stream([]map[string]any{
			step(map[string]any{"role": "assistant", "content": "Answer.", "model": "m"}),
			{"type": "chat_finished", "data": map[string]any{"type": "chat_finished"}}}, sent)
		out, errOut, code := runWithStdin("[Team Chat] hi", "agent", "chat", "--json", "--team-tool", "/nowhere/pantheon-team")
		lines := turnLines(t, out)
		done := lines[len(lines)-1]
		mu.Lock()
		if !stays {
			if code != 0 || done.Type != "done" || done.Status != "finished" || chats != 2 || cursors < 3 ||
				!strings.Contains(out, `"text":"Answer."`) {
				t.Fatalf("chats=%d cursors=%d %d %s %s", chats, cursors, code, out, errOut)
			}
		} else if done.Status != "error" || !strings.Contains(done.Error, "still busy with earlier work") || chats != 1 ||
			strings.Contains(out, "queued") {
			t.Fatalf("stays busy: chats=%d %s", chats, out)
		}
		mu.Unlock()
		closed := false
		for _, m := range f.called() {
			closed = closed || m == "team_turn_close"
		}
		if !closed {
			t.Fatalf("the conversation's channel was not closed: %v", f.called())
		}
	}
}
