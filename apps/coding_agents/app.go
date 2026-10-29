// Package codingagentsapp is the Go builtin behind the Coding Agents App: it
// lists the coding-agent CLIs installed on its node, runs their headless
// mode one turn at a time, and keeps each conversation's events so a window
// can replay them. See docs/apps/coding-agents.md.
package codingagentsapp

import (
	"bufio"
	"crypto/rand"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"os"
	"os/exec"
	"path/filepath"
	"sort"
	"strings"
	"sync"
	"time"

	"github.com/aristoteleo/pantheonos/os/codingagents"
	"github.com/nats-io/nats.go"
)

const (
	stderrKeep   = 8 * 1024
	replayBudget = 384 * 1024 // bytes of events per agent_conversation reply
	maxLine      = 16 << 20
	stopGrace    = 3 * time.Second
	titleLimit   = 80
)

// Conversation is one thread with one agent in one directory.
type Conversation struct {
	ID        string  `json:"id"`
	Agent     string  `json:"agent"`
	Cwd       string  `json:"cwd"`
	Title     string  `json:"title"`
	Session   string  `json:"session_id,omitempty"` // the agent's own id
	Model     string  `json:"model,omitempty"`
	Auto      bool    `json:"auto"`
	Origin    string  `json:"origin,omitempty"` // ui|agent|cli
	Created   float64 `json:"created"`
	Updated   float64 `json:"updated"`
	Status    string  `json:"status"` // idle|running|error
	LastError string  `json:"last_error,omitempty"`
	Seq       int64   `json:"seq"`
	// Limits is the account quota the agent last reported, with its time.
	Limits   map[string]codingagents.Limit `json:"limits,omitempty"`
	LimitsAt float64                       `json:"limits_at,omitempty"`
}

// agentLimits is the last quota an agent reported on this node.
type agentLimits struct {
	Limits map[string]codingagents.Limit `json:"limits"`
	At     float64                       `json:"at"`
}

type turn struct {
	cmd     *exec.Cmd
	done    chan struct{}
	stopped bool
	started time.Time
}

// App holds the conversations on this node and the turns running now.
type App struct {
	root   string
	nc     *nats.Conn
	prefix string

	// The node this service runs on, for what agents are told.
	NodeID, NodeName string

	mu        sync.Mutex
	convs     map[string]*Conversation
	running   map[string]*turn
	limits    map[string]agentLimits
	plans     map[string]string
	terminals map[string]*TerminalSession
	hooks     *hookServer
	logMu     sync.Mutex        // serialises event-log appends
	quotaNote map[string]string // why an agent's quota is unknown
	quotaBusy map[string]bool   // a quota read under way, per agent
}

// DefaultRoot is where a node keeps its conversations.
func DefaultRoot() string { return codingagents.Root() }

// NewApp loads the conversations under root. A turn that was running when
// the service last stopped is marked interrupted.
func NewApp(nc *nats.Conn, prefix, root string) *App {
	a := &App{root: root, nc: nc, prefix: prefix, convs: map[string]*Conversation{}, running: map[string]*turn{},
		limits: map[string]agentLimits{}, plans: map[string]string{}, terminals: map[string]*TerminalSession{},
		quotaNote: map[string]string{}, quotaBusy: map[string]bool{}}
	if host, err := os.Hostname(); err == nil {
		a.NodeName = host
	}
	if b, err := os.ReadFile(a.indexPath()); err == nil {
		var list []*Conversation
		if json.Unmarshal(b, &list) == nil {
			for _, c := range list {
				if c.Status == "running" {
					c.Status, c.LastError = "error", "Interrupted: the node's Coding Agents service restarted"
				}
				a.convs[c.ID] = c
				if c.Limits != nil && c.LimitsAt > a.limits[c.Agent].At {
					a.limits[c.Agent] = agentLimits{c.Limits, c.LimitsAt}
				}
			}
		}
	}
	return a
}

func (a *App) indexPath() string            { return filepath.Join(a.root, "conversations.json") }
func (a *App) logPath(id string) string     { return filepath.Join(a.root, id+".jsonl") }
func streamID(conversationID string) string { return "coding_" + conversationID }

func (a *App) streamSubject(id string) string {
	base := "pantheon.stream." + streamID(id)
	if a.prefix != "" {
		return a.prefix + "." + base
	}
	return base
}

func now() float64 { return float64(time.Now().UnixNano()) / 1e9 }

func newID() string {
	b := make([]byte, 8)
	_, _ = rand.Read(b)
	return hex.EncodeToString(b)
}

// saveLocked writes the index; a.mu is held.
func (a *App) saveLocked() error {
	list := make([]*Conversation, 0, len(a.convs))
	for _, c := range a.convs {
		list = append(list, c)
	}
	sort.Slice(list, func(i, j int) bool { return list[i].Updated > list[j].Updated })
	b, err := json.MarshalIndent(list, "", " ")
	if err != nil {
		return err
	}
	if err := os.MkdirAll(a.root, 0o700); err != nil {
		return err
	}
	tmp := a.indexPath() + ".tmp"
	if err := os.WriteFile(tmp, b, 0o600); err != nil {
		return err
	}
	return os.Rename(tmp, a.indexPath())
}

func failure(format string, args ...any) map[string]any {
	return map[string]any{"success": false, "error": fmt.Sprintf(format, args...)}
}

// ---- detection -------------------------------------------------------------

func shellQuote(s string) string {
	if s != "" && strings.IndexFunc(s, func(r rune) bool {
		return !(r == '/' || r == '.' || r == '-' || r == '_' || r == '=' || r == ':' || r == '@' || r == '+' ||
			r >= '0' && r <= '9' || r >= 'a' && r <= 'z' || r >= 'A' && r <= 'Z')
	}) < 0 {
		return s
	}
	return "'" + strings.ReplaceAll(s, "'", `'\''`) + "'"
}

func shellJoin(argv []string) string {
	q := make([]string, len(argv))
	for i, s := range argv {
		q[i] = shellQuote(s)
	}
	return strings.Join(q, " ")
}

// ---- turns -----------------------------------------------------------------

// SendArgs starts a turn.
type SendArgs struct {
	Agent          string
	Cwd            string
	Prompt         string
	ConversationID string
	Model          string
	Auto           bool
	Origin         string
	// Session continues a session the agent already has (one started
	// outside PantheonOS) as a new conversation; Title names it.
	Session string
	Title   string
}

func (a *App) send(p SendArgs) map[string]any {
	prompt := strings.TrimSpace(p.Prompt)
	if prompt == "" {
		return failure("Write a message for the agent")
	}
	a.mu.Lock()
	var c *Conversation
	if p.ConversationID != "" {
		c = a.convs[p.ConversationID]
		if c == nil {
			a.mu.Unlock()
			return failure("No conversation %s on this node", p.ConversationID)
		}
		if a.running[c.ID] != nil {
			a.mu.Unlock()
			return failure("The agent is still working on the previous message")
		}
		if p.Model != "" {
			c.Model = p.Model
		}
	} else {
		agent, ok := codingagents.Lookup(p.Agent)
		if !ok || agent.Headless == nil {
			a.mu.Unlock()
			return failure("%q has no conversation mode; open it in a terminal", p.Agent)
		}
		if p.Session != "" {
			for _, other := range a.convs {
				if other.Agent == p.Agent && other.Session == p.Session && a.running[other.ID] != nil {
					a.mu.Unlock()
					return failure("That session is working in another conversation")
				}
			}
		}
		cwd := p.Cwd
		if cwd == "" {
			cwd, _ = os.UserHomeDir()
		}
		if st, err := os.Stat(cwd); err != nil || !st.IsDir() {
			a.mu.Unlock()
			return failure("%s is not a folder on this node", cwd)
		}
		title := firstLine(firstNonEmpty(p.Title, prompt))
		if len([]rune(title)) > titleLimit {
			title = string([]rune(title)[:titleLimit]) + "…"
		}
		origin := p.Origin
		if origin == "" {
			origin = "ui"
		}
		c = &Conversation{ID: newID(), Agent: p.Agent, Cwd: cwd, Title: title, Model: p.Model, Auto: p.Auto,
			Origin: origin, Created: now(), Status: "idle", Session: p.Session}
		a.convs[c.ID] = c
	}
	agent, _ := codingagents.Lookup(c.Agent)
	r, err := resolve(c.Agent)
	if err != nil {
		a.mu.Unlock()
		return failure("%v", err)
	}
	launch := codingagents.Launch{Agent: c.Agent, Conversation: c.ID, NodeID: a.NodeID, NodeName: a.NodeName}
	note := codingagents.Note(r.settings, launch)
	agentPrompt := prompt
	if note != "" && agent.Instructions == nil && c.Seq == 0 && c.Session == "" {
		// Without a way to add instructions, the first prompt carries them.
		agentPrompt = "[" + note + "]\n\n" + prompt
	}
	model := c.Model
	if model == "" {
		model = r.settings.Agents[c.Agent].Model
	}
	t := codingagents.Turn{Prompt: agentPrompt, Session: c.Session, Model: model, Cwd: c.Cwd, Auto: c.Auto}
	args := append(agent.Headless.Args(t), r.settings.Agents[c.Agent].Args...)
	if note != "" && agent.Instructions != nil {
		args = append(args, agent.Instructions(note)...)
	}
	cmd := exec.Command(r.path, args...)
	cmd.Dir = c.Cwd
	cmd.Env = codingagents.LaunchEnv(r.settings, launch)
	if !agent.Headless.PromptArg {
		cmd.Stdin = strings.NewReader(agentPrompt)
	}
	setProcessGroup(cmd)
	stdout, err := cmd.StdoutPipe()
	if err != nil {
		a.mu.Unlock()
		return failure("%v", err)
	}
	stderr := &tailBuffer{max: stderrKeep}
	cmd.Stderr = stderr

	c.Status, c.LastError, c.Updated = "running", "", now()
	userSeq := c.Seq + 1
	tr := &turn{cmd: cmd, done: make(chan struct{}), started: time.Now()}
	a.running[c.ID] = tr
	_ = a.saveLocked()
	a.mu.Unlock()

	a.emit(c.ID, codingagents.Event{Type: "user", Text: prompt})
	if err := cmd.Start(); err != nil {
		a.finish(c.ID, tr, codingagents.Event{Type: "done", Error: fmt.Sprintf("Could not start %s: %v", agent.Label, err)})
		return failure("Could not start %s: %v", agent.Label, err)
	}
	go a.pump(c.ID, tr, agent, stdout, stderr)
	return map[string]any{"success": true, "conversation_id": c.ID, "stream_id": streamID(c.ID),
		"from_seq": userSeq, "conversation": a.snapshot(c.ID)}
}

func (a *App) pump(id string, tr *turn, agent codingagents.Agent, stdout io.Reader, stderr *tailBuffer) {
	parser := agent.Headless.Parser()
	sc := bufio.NewScanner(stdout)
	sc.Buffer(make([]byte, 64*1024), maxLine)
	var last *codingagents.Event
	for sc.Scan() {
		for _, e := range parser.Parse(sc.Bytes()) {
			if e.Type == "done" {
				ev := e
				last = &ev
				continue // emitted once the process has exited
			}
			if e.Type == "limits" {
				a.mu.Lock()
				if c := a.convs[id]; c != nil {
					c.Limits, c.LimitsAt = e.Limits, now()
					a.limits[c.Agent] = agentLimits{e.Limits, c.LimitsAt}
				}
				a.mu.Unlock()
			}
			if e.Type == "session" {
				a.mu.Lock()
				if c := a.convs[id]; c != nil {
					if e.SessionID != "" {
						c.Session = e.SessionID
					}
					if e.Model != "" && c.Model == "" {
						c.Model = e.Model
					}
					_ = a.saveLocked()
				}
				a.mu.Unlock()
			}
			a.emit(id, e)
		}
	}
	_, _ = io.Copy(io.Discard, stdout)
	waitErr := tr.cmd.Wait()
	end := codingagents.Event{Type: "done"}
	if last != nil {
		end = *last
	}
	if end.DurationMs == 0 {
		end.DurationMs = time.Since(tr.started).Milliseconds()
	}
	switch {
	case tr.stopped:
		end.Error, end.Stopped = "", true
	case end.Error == "" && waitErr != nil && last == nil:
		end.Error = exitMessage(agent.Label, waitErr, stderr.String())
	}
	a.finish(id, tr, end)
}

func exitMessage(label string, err error, stderr string) string {
	msg := fmt.Sprintf("%s exited: %v", label, err)
	if s := strings.TrimSpace(stderr); s != "" {
		lines := strings.Split(s, "\n")
		if len(lines) > 12 {
			lines = lines[len(lines)-12:]
		}
		msg += "\n" + strings.Join(lines, "\n")
	}
	return msg
}

func (a *App) finish(id string, tr *turn, end codingagents.Event) {
	a.emit(id, end)
	a.mu.Lock()
	if c := a.convs[id]; c != nil {
		c.Status, c.LastError, c.Updated = "idle", "", now()
		if end.Error != "" {
			c.Status, c.LastError = "error", firstLine(end.Error)
		}
		_ = a.saveLocked()
	}
	if a.running[id] == tr {
		delete(a.running, id)
	}
	a.mu.Unlock()
	close(tr.done)
}

// emit numbers an event, appends it to the conversation's log and publishes
// it on the conversation's stream.
func (a *App) emit(id string, e codingagents.Event) {
	a.mu.Lock()
	c := a.convs[id]
	if c == nil {
		a.mu.Unlock()
		return
	}
	c.Seq++
	e.Seq = c.Seq
	a.mu.Unlock()
	e.Time = now()
	b, err := json.Marshal(e)
	if err != nil {
		return
	}
	a.logMu.Lock()
	if err := os.MkdirAll(a.root, 0o700); err == nil {
		if f, err := os.OpenFile(a.logPath(id), os.O_CREATE|os.O_APPEND|os.O_WRONLY, 0o600); err == nil {
			_, _ = f.Write(append(b, '\n'))
			_ = f.Close()
		}
	}
	a.logMu.Unlock()
	if a.nc == nil {
		return
	}
	msg, err := json.Marshal(map[string]any{
		"type": "custom", "session_id": streamID(id), "timestamp": e.Time, "metadata": map[string]any{},
		"data": map[string]any{"type": "coding.event", "conversation_id": id, "event": json.RawMessage(b)},
	})
	if err == nil {
		_ = a.nc.Publish(a.streamSubject(id), msg)
	}
}

func (a *App) stop(id string) map[string]any {
	a.mu.Lock()
	tr := a.running[id]
	if tr != nil {
		tr.stopped = true
	}
	a.mu.Unlock()
	if tr == nil {
		return map[string]any{"success": true, "running": false}
	}
	interrupt(tr.cmd)
	select {
	case <-tr.done:
	case <-time.After(stopGrace):
		kill(tr.cmd)
		<-tr.done
	}
	return map[string]any{"success": true, "running": false}
}

// ---- reading ---------------------------------------------------------------

func (a *App) snapshot(id string) map[string]any {
	a.mu.Lock()
	defer a.mu.Unlock()
	c := a.convs[id]
	if c == nil {
		return nil
	}
	b, _ := json.Marshal(c)
	var m map[string]any
	_ = json.Unmarshal(b, &m)
	return m
}

func (a *App) list(agent, cwd string) map[string]any {
	a.mu.Lock()
	out := []*Conversation{}
	for _, c := range a.convs {
		if (agent == "" || c.Agent == agent) && (cwd == "" || c.Cwd == cwd) {
			cp := *c
			out = append(out, &cp)
		}
	}
	a.mu.Unlock()
	sort.Slice(out, func(i, j int) bool { return out[i].Updated > out[j].Updated })
	return map[string]any{"success": true, "conversations": out}
}

// conversation returns events after seq, merging streamed pieces and
// stopping near the reply budget; more says another call has the rest.
func (a *App) conversation(id string, after int64) map[string]any {
	meta := a.snapshot(id)
	if meta == nil {
		return failure("No conversation %s on this node", id)
	}
	a.logMu.Lock()
	f, err := os.Open(a.logPath(id))
	a.logMu.Unlock()
	events := []codingagents.Event{}
	more := false
	if err == nil {
		defer f.Close()
		sc := bufio.NewScanner(f)
		sc.Buffer(make([]byte, 64*1024), maxLine)
		size := 0
		for sc.Scan() {
			var e codingagents.Event
			if json.Unmarshal(sc.Bytes(), &e) != nil || e.Seq <= after {
				continue
			}
			if n := len(events); n > 0 && e.Delta && events[n-1].Delta && events[n-1].Type == e.Type {
				events[n-1].Text += e.Text
				events[n-1].Seq = e.Seq
				size += len(e.Text)
				continue
			}
			if size > replayBudget {
				more = true
				break
			}
			events = append(events, e)
			size += len(sc.Bytes())
		}
	}
	return map[string]any{"success": true, "conversation": meta, "events": events, "more": more}
}

func (a *App) remove(id string) map[string]any {
	a.stop(id)
	a.mu.Lock()
	if a.convs[id] == nil {
		a.mu.Unlock()
		return failure("No conversation %s on this node", id)
	}
	delete(a.convs, id)
	_ = a.saveLocked()
	a.mu.Unlock()
	a.logMu.Lock()
	err := os.Remove(a.logPath(id))
	a.logMu.Unlock()
	if err != nil && !errors.Is(err, os.ErrNotExist) {
		return failure("%v", err)
	}
	return map[string]any{"success": true}
}

// Close stops every running turn and the hook endpoint.
func (a *App) Close() {
	defer a.closeHooks()
	a.mu.Lock()
	ids := make([]string, 0, len(a.running))
	for id := range a.running {
		ids = append(ids, id)
	}
	a.mu.Unlock()
	for _, id := range ids {
		a.stop(id)
	}
}

func firstLine(s string) string {
	s = strings.TrimSpace(s)
	if i := strings.IndexByte(s, '\n'); i >= 0 {
		return strings.TrimSpace(s[:i]) + " …"
	}
	return s
}

// tailBuffer keeps the last max bytes written to it.
type tailBuffer struct {
	mu  sync.Mutex
	max int
	b   []byte
}

func (t *tailBuffer) Write(p []byte) (int, error) {
	t.mu.Lock()
	t.b = append(t.b, p...)
	if over := len(t.b) - t.max; over > 0 {
		t.b = t.b[over:]
	}
	t.mu.Unlock()
	return len(p), nil
}

func (t *tailBuffer) String() string {
	t.mu.Lock()
	defer t.mu.Unlock()
	return string(t.b)
}
