package codingagentsapp

import (
	"context"
	"crypto/subtle"
	"encoding/json"
	"fmt"
	"io"
	"net"
	"net/http"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"time"

	"github.com/aristoteleo/pantheonos/os/codingagents"
)

// TerminalStream carries every terminal session's state changes.
const TerminalStream = "coding_terminals"

const terminalKeep = 24 * time.Hour

// TerminalSession is an agent PantheonOS opened in a Terminal window. Its
// state comes from the hooks given to that launch.
type TerminalSession struct {
	ID     string `json:"id"`
	NodeID string `json:"node_id,omitempty"`
	Agent  string `json:"agent"`
	Label  string `json:"label"`
	Cwd    string `json:"cwd,omitempty"`
	State  string `json:"state"` // starting|ready|working|waiting|ended
	Detail string `json:"detail,omitempty"`
	// Since: when the state began. Source: what said it — "hook" (the
	// agent's own events), "activity" (its screen: output that keeps
	// changing it, or has settled), "exit" (its process or terminal ended).
	Since     float64                       `json:"since,omitempty"`
	Source    string                        `json:"source,omitempty"`
	SessionID string                        `json:"session_id,omitempty"` // the agent's own id
	Title     string                        `json:"title,omitempty"`      // the first prompt
	Created   float64                       `json:"created"`
	Updated   float64                       `json:"updated"`
	Limits    map[string]codingagents.Limit `json:"limits,omitempty"`
	// Exited: the agent's process is gone and its shell is left (state
	// ended, for good); ExitCode is its exit status.
	Exited   bool `json:"exited,omitempty"`
	ExitCode *int `json:"exit_code,omitempty"`
	// Task is set for a terminal that runs a task for the agent (install,
	// login, update) rather than the agent itself.
	Task string `json:"task,omitempty"`
	// hooked: the agent's own events say when it works (its hooks reached
	// the service, or it reports its state to its terminal), so its screen
	// says nothing more about it. Codex's notify alone is not that: it only
	// reports a finished turn.
	hooked bool
}

// hookServer receives the hooks of terminal launches on this machine only.
type hookServer struct {
	url, token string
	srv        *http.Server
}

func (a *App) newTerminal(agent, label, cwd string) *TerminalSession {
	t := &TerminalSession{ID: "t" + newID(), NodeID: a.NodeID, Agent: agent, Label: label, Cwd: cwd, State: "starting",
		Created: now(), Updated: now(), Since: now()}
	a.mu.Lock()
	for id, old := range a.terminals {
		if old.Updated < now()-terminalKeep.Seconds() {
			delete(a.terminals, id)
			codingagents.ForgetTerminal(id)
		}
	}
	a.terminals[t.ID] = t
	a.mu.Unlock()
	return t
}

func (a *App) hookServer() (*hookServer, error) {
	a.mu.Lock()
	defer a.mu.Unlock()
	if a.hooks != nil {
		return a.hooks, nil
	}
	ln, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		return nil, err
	}
	h := &hookServer{url: "http://" + ln.Addr().String(), token: newID() + newID()}
	mux := http.NewServeMux()
	mux.HandleFunc("POST /hook/{terminal}/{event}", func(w http.ResponseWriter, r *http.Request) {
		if subtle.ConstantTimeCompare([]byte(r.Header.Get("X-Pantheon-Hook")), []byte(h.token)) != 1 {
			http.Error(w, "forbidden", http.StatusForbidden)
			return
		}
		body, _ := io.ReadAll(io.LimitReader(r.Body, 1<<20))
		var payload map[string]any
		_ = json.Unmarshal(body, &payload)
		a.onHook(r.PathValue("terminal"), r.PathValue("event"), payload)
		w.WriteHeader(http.StatusNoContent)
	})
	h.srv = &http.Server{Handler: mux, ReadHeaderTimeout: 5 * time.Second}
	go func() { _ = h.srv.Serve(ln) }()
	a.hooks = h
	return h, nil
}

// onHook moves a terminal session along and tells the desktop.
func (a *App) onHook(id, event string, payload map[string]any) {
	a.mu.Lock()
	t := a.terminals[id]
	if t == nil {
		a.mu.Unlock()
		return
	}
	changed := false
	// Claude Code's hooks say session_id; Codex's notify, thread-id.
	sid, _ := payload["session_id"].(string)
	if sid == "" {
		sid, _ = payload["thread-id"].(string)
	}
	if t.Title == "" {
		if title := promptTitle(payload); title != "" {
			t.Title, changed = title, true
		}
	}
	if sid != "" && (sid != t.SessionID || changed) {
		t.SessionID, changed = sid, true
		k := knownSession{Agent: t.Agent, ID: sid, Cwd: t.Cwd, Title: t.Title}
		go func() { _ = a.remember(k) }()
	}
	switch {
	case t.Exited:
		// A hook that lands after the agent is gone (Codex's notify runs
		// apart from it) says nothing about it any more.
	case event == codingagents.ExitHookEvent:
		code, ok := payload["exit_code"].(float64)
		if !ok {
			code = -1
		}
		c := int(code)
		t.State, t.Exited, t.ExitCode, t.Detail, changed = "ended", true, &c, "", true
		t.Since, t.Source = now(), "exit"
		if c != 0 {
			t.Detail = fmt.Sprintf("exit status %d", c)
		}
	case event == "StatusLine":
		if limits := codingagents.StatusLineLimits(payload); len(limits) > 0 {
			t.Limits = limits
			a.limits[t.Agent] = agentLimits{limits, now()}
			changed = true
		}
	default:
		state := codingagents.HookState(t.Agent, event, payload)
		if state != "" && t.Agent != "codex" {
			t.hooked = true
		}
		if state != "" && state != t.State {
			t.State, changed = state, true
			t.Since, t.Source = now(), "hook"
			t.Detail = ""
			if state == "waiting" {
				t.Detail = firstNonEmpty(str(payload["message"]), str(payload["tool_name"]))
			}
		}
	}
	if !changed {
		a.mu.Unlock()
		return
	}
	a.changedTerminal(t)
}

// changedTerminal tells the desktop a terminal session's new state; a.mu is
// held, and released.
func (a *App) changedTerminal(t *TerminalSession) {
	t.Updated = now()
	snap := *t
	a.mu.Unlock()
	if snap.State == "ended" {
		_ = os.Remove(filepath.Join(a.root, "terminals", snap.ID+".settings.json"))
		codingagents.ForgetTerminal(snap.ID)
	}
	a.publish(TerminalStream, map[string]any{"type": "coding.terminal", "terminal": snap})
}

// watchActivity has the screen of the PTY that runs a terminal launch's
// command line report for its session (os/codingagents/activity.go).
func (a *App) watchActivity(command, terminal string) {
	codingagents.WatchCommand(command, terminal, a.onActivity)
}

// onActivity moves a terminal session along by what its terminal shows. The
// state an agent reports to its terminal (OSC 7501, Codex's) counts as its
// own events do. The screen counts for an agent whose own events say
// nothing (none reached the service, or Codex's notify, which only reports a
// finished turn): working while it keeps changing, ready once it settles. A
// wait for the user stays until the agent works again; a notification
// asking for approval or input is such a wait, and says what it is.
func (a *App) onActivity(act codingagents.Activity) {
	a.mu.Lock()
	t := a.terminals[act.Terminal]
	if t == nil || t.Exited || t.State == "ended" {
		a.mu.Unlock()
		return
	}
	state, detail, source := "", "", "activity"
	switch {
	case act.Closed:
		state, detail, source = "ended", "Terminal closed", "exit"
	case act.State != "":
		t.hooked, source = true, "hook"
		state = act.State
		if state == "waiting" {
			detail = waitKind(act.Detail)
			if t.State == "waiting" && t.Detail != "" {
				// A notification already said what it waits for.
				detail = t.Detail
			}
		}
	case act.Notice != "":
		if asksForUser(act.Notice) && (!t.hooked || t.Agent == "codex") {
			state, detail, source = "waiting", act.Notice, "hook"
		}
	case t.hooked:
	case act.Working:
		state = "working"
	case t.State == "working" || t.State == "starting":
		state = "ready"
	}
	if state == "" || state == t.State && detail == t.Detail {
		a.mu.Unlock()
		return
	}
	t.State, t.Detail, t.Since, t.Source = state, detail, now(), source
	a.changedTerminal(t)
}

// waitKind says what an agent's OSC 7501 kind waits for.
func waitKind(kind string) string {
	switch kind {
	case "":
		return ""
	case "permission":
		return "Permission"
	}
	return strings.ToUpper(kind[:1]) + kind[1:]
}

// asksForUser: a notification that waits for the user rather than tells
// them something finished.
func asksForUser(notice string) bool {
	n := strings.ToLower(notice)
	for _, w := range []string{"approval", "approve", "permission", "confirm", "input", "question", "requested", "waiting for"} {
		if strings.Contains(n, w) {
			return true
		}
	}
	return false
}

// promptTitle is a session's title from a hook: the prompt that began it.
// Claude Code's UserPromptSubmit says prompt, Codex's notify input-messages,
// the Pantheon Agent title.
func promptTitle(payload map[string]any) string {
	text := firstNonEmpty(str(payload["title"]), str(payload["prompt"]))
	if msgs, ok := payload["input-messages"].([]any); text == "" && ok && len(msgs) > 0 {
		text = str(msgs[0])
	}
	text = strings.Join(strings.Fields(text), " ")
	if r := []rune(text); len(r) > 80 {
		text = string(r[:79]) + "…"
	}
	return text
}

func str(v any) string {
	s, _ := v.(string)
	return s
}

// terminalList is the terminal sessions of the last day, newest first.
func (a *App) terminalList() map[string]any {
	a.mu.Lock()
	out := []TerminalSession{}
	for _, t := range a.terminals {
		out = append(out, *t)
	}
	a.mu.Unlock()
	sort.Slice(out, func(i, j int) bool { return out[i].Updated > out[j].Updated })
	return map[string]any{"success": true, "terminals": out}
}

// publish sends one message on a stream of this service.
func (a *App) publish(stream string, data map[string]any) {
	if a.nc == nil {
		return
	}
	subject := "pantheon.stream." + stream
	if a.prefix != "" {
		subject = a.prefix + "." + subject
	}
	msg, err := json.Marshal(map[string]any{"type": "custom", "session_id": stream, "timestamp": now(),
		"metadata": map[string]any{}, "data": data})
	if err == nil {
		_ = a.nc.Publish(subject, msg)
	}
}

func (a *App) closeHooks() {
	a.mu.Lock()
	h := a.hooks
	a.hooks = nil
	a.mu.Unlock()
	if h != nil {
		ctx, cancel := context.WithTimeout(context.Background(), time.Second)
		defer cancel()
		_ = h.srv.Shutdown(ctx)
	}
	// Launch files of sessions that will never report again.
	if entries, err := os.ReadDir(filepath.Join(a.root, "terminals")); err == nil {
		for _, e := range entries {
			if strings.HasSuffix(e.Name(), ".settings.json") {
				_ = os.Remove(filepath.Join(a.root, "terminals", e.Name()))
			}
		}
	}
}

func firstNonEmpty(v ...string) string {
	for _, s := range v {
		if s != "" {
			return s
		}
	}
	return ""
}
