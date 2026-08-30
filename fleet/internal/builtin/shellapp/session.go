// Package shellapp is the Go builtin implementation of the `shell` App —
// the first of the python-free batch compiled into the fleet runner. It
// mirrors pantheon/toolsets/shell (ShellToolSet + AsyncShell) behavior:
// persistent shell sessions, the end-marker protocol, timeout-keeps-running
// semantics with later drain, and per-chat session keying.
package shellapp

import (
	"bufio"
	"crypto/rand"
	"encoding/hex"
	"fmt"
	"io"
	"os"
	"os/exec"
	"strings"
	"sync"
	"time"
)

// session is one live shell process (Python's AsyncShell): commands are
// written with a trailing `echo <marker>` and output is read line-wise until
// the marker; a timeout returns partial output and leaves the marker armed
// so a later call can drain the rest.
type session struct {
	cmd    *exec.Cmd
	stdin  io.WriteCloser
	lines  chan string
	closed chan struct{}

	mu            sync.Mutex
	busy          bool
	currentMarker string
}

func defaultShell() string {
	if sh := os.Getenv("SHELL"); sh != "" {
		if _, err := os.Stat(sh); err == nil {
			return sh
		}
	}
	for _, sh := range []string{"/bin/bash", "/usr/bin/bash", "/bin/zsh", "/usr/bin/zsh", "/bin/sh"} {
		if _, err := os.Stat(sh); err == nil {
			return sh
		}
	}
	return "/bin/bash"
}

func newMarker() string {
	b := make([]byte, 16)
	_, _ = rand.Read(b)
	return "__COMMAND_END_" + hex.EncodeToString(b) + "__"
}

// startSession launches the shell in workdir with stderr merged into stdout,
// mirroring AsyncShell (PS1 cleared; parent env inherited).
func startSession(workdir string) (*session, error) {
	cmd := exec.Command(defaultShell())
	cmd.Dir = workdir
	cmd.Env = append(os.Environ(), "PS1=")
	stdin, err := cmd.StdinPipe()
	if err != nil {
		return nil, err
	}
	stdout, err := cmd.StdoutPipe()
	if err != nil {
		return nil, err
	}
	cmd.Stderr = cmd.Stdout // merged, like subprocess stderr=STDOUT
	if err := cmd.Start(); err != nil {
		return nil, err
	}
	s := &session{
		cmd:    cmd,
		stdin:  stdin,
		lines:  make(chan string, 4096),
		closed: make(chan struct{}),
	}
	go func() {
		defer close(s.closed)
		sc := bufio.NewScanner(stdout)
		sc.Buffer(make([]byte, 64*1024), 8*1024*1024)
		for sc.Scan() {
			select {
			case s.lines <- sc.Text():
			default:
				// Reader saturated: drop oldest to keep the shell from
				// blocking on a full pipe.
				select {
				case <-s.lines:
				default:
				}
				s.lines <- sc.Text()
			}
		}
	}()
	go func() { _ = cmd.Wait() }()
	return s, nil
}

func (s *session) alive() bool {
	select {
	case <-s.closed:
		return false
	default:
	}
	return s.cmd.ProcessState == nil
}

func (s *session) idle() bool {
	s.mu.Lock()
	defer s.mu.Unlock()
	return !s.busy
}

// run writes the command with an end marker and reads until the marker or
// timeout (0 = no timeout). Mirrors AsyncShell.run_command.
func (s *session) run(command string, timeout time.Duration) (string, bool, error) {
	marker := newMarker()
	s.mu.Lock()
	s.busy = true
	s.currentMarker = marker
	s.mu.Unlock()

	var full string
	if strings.Contains(command, "<<") {
		full = command + "\necho " + marker + "\n"
	} else {
		full = command + "; echo " + marker + "\n"
	}
	if _, err := io.WriteString(s.stdin, full); err != nil {
		return "", false, fmt.Errorf("broken pipe to shell process: %w", err)
	}
	out, finished := s.readUntil(marker, timeout)
	if finished {
		s.mu.Lock()
		s.busy = false
		s.currentMarker = ""
		s.mu.Unlock()
	}
	return out, finished, nil
}

// drain reads buffered output up to the armed marker (Python's
// read_until_marker with command=None). With no armed marker it collects
// whatever is buffered within the timeout.
func (s *session) drain(timeout time.Duration) (string, bool) {
	if timeout <= 0 {
		// A markerless, timeoutless drain would block forever; the Python
		// callers always bound this (get_shell_output defaults to 5s).
		timeout = 5 * time.Second
	}
	s.mu.Lock()
	marker := s.currentMarker
	s.mu.Unlock()
	out, finished := s.readUntil(marker, timeout)
	if finished && marker != "" {
		s.mu.Lock()
		s.busy = false
		s.currentMarker = ""
		s.mu.Unlock()
	}
	return out, finished
}

// readUntil consumes lines until the marker line (filtered out, like
// filter_out_line) or the timeout. An empty marker means "no marker": read
// until the timeout, and report finished so the caller treats it as done.
func (s *session) readUntil(marker string, timeout time.Duration) (string, bool) {
	var b strings.Builder
	var deadline <-chan time.Time
	if timeout > 0 {
		t := time.NewTimer(timeout)
		defer t.Stop()
		deadline = t.C
	}
	for {
		select {
		case line, ok := <-s.lines:
			if !ok {
				return b.String(), marker == ""
			}
			if marker != "" && strings.Contains(line, marker) {
				return b.String(), true
			}
			b.WriteString(line)
			b.WriteString("\n")
		case <-deadline:
			if marker != "" {
				b.WriteString("\n[Warning] Timeout waiting for marker.")
				return b.String(), false
			}
			return b.String(), true
		case <-s.closed:
			// Flush what the scanner already queued, then report.
			for {
				select {
				case line := <-s.lines:
					if marker != "" && strings.Contains(line, marker) {
						return b.String(), true
					}
					b.WriteString(line)
					b.WriteString("\n")
				default:
					return b.String(), marker == ""
				}
			}
		}
	}
}

func (s *session) close() {
	_, _ = io.WriteString(s.stdin, "exit\n")
	select {
	case <-s.closed:
	case <-time.After(2 * time.Second):
		if s.cmd.Process != nil {
			_ = s.cmd.Process.Kill()
		}
	}
}
