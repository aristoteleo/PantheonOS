//go:build !windows

// Package ptyapp is the Go builtin implementation of the `pty` App — real
// pseudo-terminal sessions for the Terminal UI, mirroring
// pantheon/toolsets/pty. Output is streamed as base64 chunks on the NATS
// stream subject `pantheon.stream.pty_<session_id>` in the StreamMessage
// JSON shape the frontend already speaks; the pty@1 tool surface
// (pty_open/write/resize/attach/list/close, all hidden) matches the Python
// toolset.
package ptyapp

import (
	"encoding/base64"
	"encoding/json"
	"fmt"
	"os"
	"os/exec"
	"sync"
	"syscall"
	"time"

	"github.com/creack/pty"
	"github.com/nats-io/nats.go"
)

const (
	readChunk        = 65536
	scrollbackCap    = 256 * 1024
	idleReapSeconds  = 60 * 60
	promptGrace      = 250 * time.Millisecond
	promptPoll       = 10 * time.Millisecond
	terminateTimeout = 3 * time.Second
)

// banner mirrors the Python terminal banner (PANTHEON_PTY_BANNER=0 disables).
const bannerText = "\x1b[38;5;110m\r\n" +
	"   ___             _   _                       ___  ____\r\n" +
	"  / _ \\ __ _ _ __ | |_| |__   ___  ___  _ __  / _ \\/ ___|\r\n" +
	" / /_)/ _` | '_ \\| __| '_ \\ / _ \\/ _ \\| '_ \\ | | | \\___ \\\r\n" +
	"/ ___/ (_| | | | | |_| | | |  __/ (_) | | | || |_| |___) |\r\n" +
	"\\/    \\__,_|_| |_|\\__|_| |_|\\___|\\___/|_| |_| \\___/|____/\r\n" +
	"\x1b[0m\x1b[38;5;66m  an agent operating system for data science\x1b[0m\r\n\r\n"

func banner() []byte {
	if os.Getenv("PANTHEON_PTY_BANNER") == "0" {
		return nil
	}
	return []byte(bannerText)
}

func pickShell() string {
	for _, c := range []string{"/bin/bash", "/usr/bin/bash", "/bin/zsh", "/bin/sh"} {
		if _, err := os.Stat(c); err == nil {
			return c
		}
	}
	return "/bin/sh"
}

// ptySession is one pseudo-terminal and the shell on it (Python PtySession).
type ptySession struct {
	id        string
	master    *os.File
	cmd       *exec.Cmd
	cols      int
	rows      int
	cwd       string
	createdAt time.Time

	chunks   chan []byte   // nil-sentinel-free; closed on EOF
	waitDone chan struct{} // closed once the single Wait() returns

	mu         sync.Mutex
	lastActive time.Time
	scrollback []byte
	exited     bool
	exitCode   *int
}

func (s *ptySession) streamID() string { return "pty_" + s.id }

func (s *ptySession) snapshot() map[string]any {
	s.mu.Lock()
	defer s.mu.Unlock()
	var code any
	if s.exitCode != nil {
		code = *s.exitCode
	}
	return map[string]any{
		"session_id": s.id,
		"stream_id":  s.streamID(),
		"cols":       s.cols,
		"rows":       s.rows,
		"cwd":        s.cwd,
		"pid":        s.cmd.Process.Pid,
		"exited":     s.exited,
		"exit_code":  code,
		"created_at": float64(s.createdAt.UnixNano()) / 1e9,
	}
}

func (s *ptySession) remember(data []byte) {
	s.mu.Lock()
	s.scrollback = append(s.scrollback, data...)
	if over := len(s.scrollback) - scrollbackCap; over > 0 {
		s.scrollback = s.scrollback[over:]
	}
	s.mu.Unlock()
}

func (s *ptySession) touch() {
	s.mu.Lock()
	s.lastActive = time.Now()
	s.mu.Unlock()
}

// App holds the pty sessions and the NATS connection they stream on.
type App struct {
	workdir string
	nc      *nats.Conn
	prefix  string

	mu       sync.Mutex
	sessions map[string]*ptySession
}

func NewApp(nc *nats.Conn, prefix, workdir string) *App {
	return &App{workdir: workdir, nc: nc, prefix: prefix,
		sessions: map[string]*ptySession{}}
}

func (a *App) streamSubject(streamID string) string {
	base := "pantheon.stream." + streamID
	if a.prefix != "" {
		return a.prefix + "." + base
	}
	return base
}

// publish sends one event in the StreamMessage wire shape. Best effort — a
// terminal that loses a frame is survivable.
func (a *App) publish(s *ptySession, payload map[string]any) {
	payload["session_id"] = s.id
	msg := map[string]any{
		"type":       "custom",
		"session_id": s.streamID(),
		"timestamp":  float64(time.Now().UnixNano()) / 1e9,
		"data":       payload,
		"metadata":   map[string]any{},
	}
	b, err := json.Marshal(msg)
	if err != nil || a.nc == nil { // nil nc: session-level tests, no bus
		return
	}
	_ = a.nc.Publish(a.streamSubject(s.streamID()), b)
}

// pump batches chunks and publishes them in order; on EOF it reports the
// exit and drops the session (a shell someone typed `exit` into).
func (a *App) pump(s *ptySession) {
	for chunk := range s.chunks {
		parts := [][]byte{chunk}
	drain:
		for {
			select {
			case more, ok := <-s.chunks:
				if !ok {
					break drain
				}
				parts = append(parts, more)
			default:
				break drain
			}
		}
		data := joinBytes(parts)
		s.remember(data)
		a.publish(s, map[string]any{
			"type": "pty.data",
			"data": base64.StdEncoding.EncodeToString(data),
		})
	}

	// EOF: the shell is gone. The waiter goroutine owns Wait; give it a
	// moment to deliver the exit code.
	select {
	case <-s.waitDone:
	case <-time.After(5 * time.Second):
	}
	var exitCode any
	s.mu.Lock()
	if s.exitCode != nil {
		exitCode = *s.exitCode
	}
	s.exited = true
	s.mu.Unlock()
	a.publish(s, map[string]any{"type": "pty.exit", "exit_code": exitCode})

	a.mu.Lock()
	if a.sessions[s.id] == s {
		delete(a.sessions, s.id)
	}
	a.mu.Unlock()
	_ = s.master.Close()
}

func joinBytes(parts [][]byte) []byte {
	n := 0
	for _, p := range parts {
		n += len(p)
	}
	out := make([]byte, 0, n)
	for _, p := range parts {
		out = append(out, p...)
	}
	return out
}

// readLoop feeds the pty's output into the chunk channel until EOF.
func (s *ptySession) readLoop() {
	defer close(s.chunks)
	buf := make([]byte, readChunk)
	for {
		n, err := s.master.Read(buf)
		if n > 0 {
			s.touch()
			chunk := make([]byte, n)
			copy(chunk, buf[:n])
			s.chunks <- chunk
		}
		if err != nil {
			return // EIO when the last slave closes = shell gone
		}
	}
}

// ---- tools ----------------------------------------------------------------

func (a *App) open(cols, rows int, cwd, shell string) map[string]any {
	a.reapIdle()
	if cols <= 0 {
		cols = 80
	}
	if rows <= 0 {
		rows = 24
	}
	target := cwd
	if target == "" {
		target = a.workdir
	}
	if st, err := os.Stat(target); err != nil || !st.IsDir() {
		target, _ = os.Getwd()
	}
	shellPath := shell
	if shellPath == "" {
		shellPath = pickShell()
	}

	cmd := exec.Command(shellPath, "-i")
	cmd.Dir = target
	env := os.Environ()
	if os.Getenv("TERM") == "" {
		env = append(env, "TERM=xterm-256color")
	}
	env = append(env, fmt.Sprintf("COLUMNS=%d", cols), fmt.Sprintf("LINES=%d", rows))
	cmd.Env = env
	// creack/pty starts the shell in its own session with the pty as its
	// controlling terminal, so job control works out of the box.

	master, err := pty.StartWithSize(cmd, &pty.Winsize{
		Rows: uint16(rows), Cols: uint16(cols),
	})
	if err != nil {
		return map[string]any{"success": false,
			"error": fmt.Sprintf("could not start %s: %v", shellPath, err)}
	}

	s := &ptySession{
		id:         newID(),
		master:     master,
		cmd:        cmd,
		cols:       cols,
		rows:       rows,
		cwd:        target,
		createdAt:  time.Now(),
		lastActive: time.Now(),
		chunks:     make(chan []byte, 1024),
		waitDone:   make(chan struct{}),
	}
	a.mu.Lock()
	a.sessions[s.id] = s
	a.mu.Unlock()
	// The ONE Wait for this process (Wait may only be called once); the
	// exit code lands on the session, and everyone else observes waitDone.
	go func() {
		_ = cmd.Wait()
		s.mu.Lock()
		if cmd.ProcessState != nil {
			code := cmd.ProcessState.ExitCode()
			s.exitCode = &code
		}
		s.mu.Unlock()
		close(s.waitDone)
	}()
	go s.readLoop()

	// Whatever the shell says before the caller could possibly subscribe —
	// the prompt — is returned, not streamed (core NATS has no replay).
	initial := append(banner(), drainInitial(s)...)
	s.remember(initial)
	go a.pump(s)

	res := map[string]any{
		"success":        true,
		"initial_output": base64.StdEncoding.EncodeToString(initial),
	}
	for k, v := range s.snapshot() {
		res[k] = v
	}
	return res
}

// drainInitial collects the shell's first output, stopping once it goes
// quiet (Python's PROMPT_GRACE_SECONDS dance).
func drainInitial(s *ptySession) []byte {
	deadline := time.Now().Add(promptGrace)
	var parts [][]byte
	for time.Now().Before(deadline) {
		select {
		case chunk, ok := <-s.chunks:
			if !ok {
				return joinBytes(parts)
			}
			parts = append(parts, chunk)
		case <-time.After(promptPoll):
			if len(parts) > 0 {
				return joinBytes(parts)
			}
		}
	}
	return joinBytes(parts)
}

func (a *App) get(sessionID string) *ptySession {
	a.mu.Lock()
	defer a.mu.Unlock()
	return a.sessions[sessionID]
}

func (a *App) write(sessionID, data string) map[string]any {
	s := a.get(sessionID)
	if s == nil {
		return map[string]any{"success": false, "error": fmt.Sprintf("no pty session '%s'", sessionID)}
	}
	s.mu.Lock()
	exited := s.exited
	s.mu.Unlock()
	if exited {
		return map[string]any{"success": false, "error": "session has exited"}
	}
	raw, err := base64.StdEncoding.DecodeString(data)
	if err != nil {
		return map[string]any{"success": false, "error": fmt.Sprintf("data is not base64: %v", err)}
	}
	if _, err := s.master.Write(raw); err != nil {
		return map[string]any{"success": false, "error": err.Error()}
	}
	s.touch()
	return map[string]any{"success": true}
}

func (a *App) resize(sessionID string, cols, rows int) map[string]any {
	s := a.get(sessionID)
	if s == nil {
		return map[string]any{"success": false, "error": fmt.Sprintf("no pty session '%s'", sessionID)}
	}
	if err := pty.Setsize(s.master, &pty.Winsize{Rows: uint16(rows), Cols: uint16(cols)}); err == nil {
		if pgid, err := syscall.Getpgid(s.cmd.Process.Pid); err == nil {
			_ = syscall.Kill(-pgid, syscall.SIGWINCH)
		}
	}
	s.mu.Lock()
	s.cols, s.rows = cols, rows
	s.mu.Unlock()
	return map[string]any{"success": true, "cols": cols, "rows": rows}
}

func (a *App) attach(sessionID string, cols, rows int) map[string]any {
	s := a.get(sessionID)
	if s == nil {
		return map[string]any{"success": false, "error": "session gone"}
	}
	s.mu.Lock()
	exited := s.exited
	scroll := make([]byte, len(s.scrollback))
	copy(scroll, s.scrollback)
	s.mu.Unlock()
	if exited {
		return map[string]any{"success": false, "error": "session gone"}
	}
	s.touch()
	if cols > 0 && rows > 0 && (cols != s.cols || rows != s.rows) {
		_ = pty.Setsize(s.master, &pty.Winsize{Rows: uint16(rows), Cols: uint16(cols)})
		s.mu.Lock()
		s.cols, s.rows = cols, rows
		s.mu.Unlock()
	}
	res := map[string]any{
		"success":    true,
		"scrollback": base64.StdEncoding.EncodeToString(scroll),
	}
	for k, v := range s.snapshot() {
		res[k] = v
	}
	return res
}

func (a *App) closeSession(sessionID string) map[string]any {
	a.mu.Lock()
	s, ok := a.sessions[sessionID]
	if ok {
		delete(a.sessions, sessionID)
	}
	a.mu.Unlock()
	if !ok {
		return map[string]any{"success": false, "error": fmt.Sprintf("no pty session '%s'", sessionID)}
	}
	a.terminate(s)
	return map[string]any{"success": true}
}

func (a *App) list() map[string]any {
	a.mu.Lock()
	sessions := make([]any, 0, len(a.sessions))
	for _, s := range a.sessions {
		sessions = append(sessions, s.snapshot())
	}
	a.mu.Unlock()
	return map[string]any{"success": true, "sessions": sessions}
}

func (a *App) terminate(s *ptySession) {
	if pgid, err := syscall.Getpgid(s.cmd.Process.Pid); err == nil {
		_ = syscall.Kill(-pgid, syscall.SIGHUP)
	}
	select {
	case <-s.waitDone:
	case <-time.After(terminateTimeout):
		_ = s.cmd.Process.Kill()
	}
	// Closing the master EOFs the read loop; the pump then publishes
	// pty.exit — the same order the Python side guarantees.
	_ = s.master.Close()
}

func (a *App) reapIdle() {
	cutoff := time.Now().Add(-idleReapSeconds * time.Second)
	a.mu.Lock()
	var stale []*ptySession
	for id, s := range a.sessions {
		s.mu.Lock()
		gone := s.exited || s.lastActive.Before(cutoff)
		s.mu.Unlock()
		if gone {
			delete(a.sessions, id)
			stale = append(stale, s)
		}
	}
	a.mu.Unlock()
	for _, s := range stale {
		go a.terminate(s)
	}
}

// Close terminates every session (instance stop).
func (a *App) Close() {
	a.mu.Lock()
	sessions := make([]*ptySession, 0, len(a.sessions))
	for _, s := range a.sessions {
		sessions = append(sessions, s)
	}
	a.sessions = map[string]*ptySession{}
	a.mu.Unlock()
	for _, s := range sessions {
		a.terminate(s)
	}
}

func newID() string {
	b := make([]byte, 6)
	f, err := os.Open("/dev/urandom")
	if err == nil {
		_, _ = f.Read(b)
		_ = f.Close()
	}
	return fmt.Sprintf("%x", b)
}
