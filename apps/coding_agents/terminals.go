package codingagentsapp

import (
	"context"
	"crypto/subtle"
	"encoding/json"
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
	ID        string                        `json:"id"`
	NodeID    string                        `json:"node_id,omitempty"`
	Agent     string                        `json:"agent"`
	Label     string                        `json:"label"`
	Cwd       string                        `json:"cwd,omitempty"`
	State     string                        `json:"state"` // starting|ready|working|waiting|ended
	Detail    string                        `json:"detail,omitempty"`
	SessionID string                        `json:"session_id,omitempty"` // the agent's own id
	Created   float64                       `json:"created"`
	Updated   float64                       `json:"updated"`
	Limits    map[string]codingagents.Limit `json:"limits,omitempty"`
}

// hookServer receives the hooks of terminal launches on this machine only.
type hookServer struct {
	url, token string
	srv        *http.Server
}

func (a *App) newTerminal(agent, label, cwd string) *TerminalSession {
	t := &TerminalSession{ID: "t" + newID(), NodeID: a.NodeID, Agent: agent, Label: label, Cwd: cwd, State: "starting",
		Created: now(), Updated: now()}
	a.mu.Lock()
	for id, old := range a.terminals {
		if old.Updated < now()-terminalKeep.Seconds() {
			delete(a.terminals, id)
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
	if sid != "" && sid != t.SessionID {
		t.SessionID, changed = sid, true
		agent := t.Agent
		go func() { _ = a.remember(knownSession{Agent: agent, ID: sid}) }()
	}
	if event == "StatusLine" {
		if limits := codingagents.StatusLineLimits(payload); len(limits) > 0 {
			t.Limits = limits
			a.limits[t.Agent] = agentLimits{limits, now()}
			changed = true
		}
	} else if state := codingagents.HookState(t.Agent, event, payload); state != "" && state != t.State {
		t.State, changed = state, true
		t.Detail = ""
		if state == "waiting" {
			t.Detail = firstNonEmpty(str(payload["message"]), str(payload["tool_name"]))
		}
	}
	if !changed {
		a.mu.Unlock()
		return
	}
	t.Updated = now()
	snap := *t
	a.mu.Unlock()
	if snap.State == "ended" {
		_ = os.Remove(filepath.Join(a.root, "terminals", snap.ID+".settings.json"))
	}
	a.publish(TerminalStream, map[string]any{"type": "coding.terminal", "terminal": snap})
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
