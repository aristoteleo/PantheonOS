// Package jobapp hosts the standard App lifecycle inside one scheduler job.
// It has no Fleet credentials, registry subscription or application-specific code.
package jobapp

import (
	"bufio"
	"context"
	"crypto/subtle"
	"encoding/json"
	"fmt"
	"io"
	"net"
	"net/http"
	"strconv"
	"sync"
	"time"

	"github.com/aristoteleo/pantheon-fleet/internal/lifecycle"
)

type Server struct {
	Manager     *lifecycle.Manager
	Token       string
	Slots       chan struct{}
	StreamSlots chan struct{}
	mu          sync.Mutex
	connections map[net.Conn]struct{}
	closed      bool
}

func New(m *lifecycle.Manager, token string) (*Server, error) {
	if len(token) != 64 {
		return nil, fmt.Errorf("a private job transport token is required")
	}
	return &Server{Manager: m, Token: token, Slots: make(chan struct{}, 16), StreamSlots: make(chan struct{}, 16), connections: map[net.Conn]struct{}{}}, nil
}

func (s *Server) ServeHTTP(w http.ResponseWriter, r *http.Request) {
	if subtle.ConstantTimeCompare([]byte(r.Header.Get("Authorization")), []byte("Bearer "+s.Token)) != 1 {
		http.Error(w, "job authorization required", http.StatusForbidden)
		return
	}
	slots := s.Slots
	if r.Method == "CONNECT" {
		slots = s.StreamSlots
	}
	select {
	case slots <- struct{}{}:
		defer func() { <-slots }()
	default:
		http.Error(w, "job connection limit reached", 503)
		return
	}
	switch {
	case r.Method == "POST" && r.URL.Path == "/control":
		body, err := io.ReadAll(http.MaxBytesReader(w, r.Body, 768<<10))
		var command lifecycle.Command
		if err == nil {
			err = lifecycle.StrictDecode(body, &command)
		}
		var out any
		if err == nil {
			out, err = s.Manager.Dispatch(r.Context(), command)
		}
		w.Header().Set("Content-Type", "application/json")
		if err != nil {
			out = map[string]string{"error": err.Error()}
		}
		_ = json.NewEncoder(w).Encode(out)
	case r.Method == "CONNECT" && r.URL.Path == "/service":
		s.connect(w, r)
	default:
		http.NotFound(w, r)
	}
}

// A viewer can only access a port declared by the exact ready App generation.
// The transport token is consumed here and never forwarded to the application.
func (s *Server) connect(w http.ResponseWriter, r *http.Request) {
	generation, err := strconv.ParseUint(r.Header.Get("X-App-Generation"), 10, 64)
	if err != nil {
		http.Error(w, "invalid generation", 400)
		return
	}
	endpoint, err := s.Manager.Service(r.Header.Get("X-App-Instance"), r.Header.Get("X-App-Revision"), generation, r.Header.Get("X-App-Component"), r.Header.Get("X-App-Port"))
	if err != nil {
		http.Error(w, err.Error(), 409)
		return
	}
	release, err := s.Manager.BeginUse(r.Header.Get("X-App-Instance"), r.Header.Get("X-App-Revision"), generation)
	if err != nil {
		http.Error(w, err.Error(), 409)
		return
	}
	defer release()
	// Service() already validated the endpoint and fixed the loopback address.
	req, err := http.NewRequest("GET", endpoint, nil)
	if err != nil {
		http.Error(w, "invalid service", 502)
		return
	}
	local, err := (&net.Dialer{Timeout: 5 * time.Second}).DialContext(r.Context(), "tcp", req.URL.Host)
	if err != nil {
		http.Error(w, "App endpoint unavailable", 502)
		return
	}
	defer local.Close()
	hijacker, ok := w.(http.Hijacker)
	if !ok {
		http.Error(w, "streaming unavailable", 500)
		return
	}
	remote, buffer, err := hijacker.Hijack()
	if err != nil {
		return
	}
	defer remote.Close()
	s.mu.Lock()
	if s.closed {
		s.mu.Unlock()
		return
	}
	s.connections[remote] = struct{}{}
	s.connections[local] = struct{}{}
	s.mu.Unlock()
	defer func() { s.mu.Lock(); delete(s.connections, remote); delete(s.connections, local); s.mu.Unlock() }()
	_, _ = buffer.WriteString("HTTP/1.1 200 Connection Established\r\n\r\n")
	if buffer.Flush() != nil {
		return
	}
	done := make(chan struct{})
	go func() { _, _ = io.Copy(local, buffer); _ = local.Close(); _ = remote.Close(); close(done) }()
	_, _ = io.Copy(remote, local)
	_ = remote.Close()
	_ = local.Close()
	<-done
}

func (s *Server) Close() {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.closed = true
	for c := range s.connections {
		_ = c.Close()
	}
}

// BufferedConn preserves bytes read ahead by the CONNECT response parser.
type BufferedConn struct {
	net.Conn
	Reader *bufio.Reader
}

func (c *BufferedConn) Read(p []byte) (int, error) { return c.Reader.Read(p) }

// StartInitial uses the same installation, dependency hooks, generation and
// readiness checks as a native node. Apps declaring runtime configuration stop
// at a durable prepared instance; the owner must configure and start that exact
// generation through /control. No App name is interpreted here.
func StartInitial(ctx context.Context, m *lifecycle.Manager, digest, scope string) (string, error) {
	for index, action := range []string{"install", "start"} {
		generation := uint64(0)
		if index > 0 {
			installation := m.Snapshot().Installations[digest]
			if installation == nil {
				return "", fmt.Errorf("initial App installation disappeared")
			}
			for _, component := range installation.Definition.Components {
				if component.Configuration != nil {
					action = "prepare_start"
					break
				}
			}
			for _, in := range m.Snapshot().Instances {
				if in.Digest == digest && in.Scope == scope {
					generation = in.Generation
				}
			}
		}
		op, err := m.Submit(lifecycle.Request{Protocol: 1, OperationID: "job-initial-" + action, Action: action, Digest: digest, Scope: scope, Generation: generation})
		if err != nil {
			return "", err
		}
		ticker := time.NewTicker(100 * time.Millisecond)
		for {
			state := m.Snapshot().Operations[op.Request.OperationID]
			if state == nil {
				ticker.Stop()
				return "", fmt.Errorf("initial App operation disappeared")
			}
			if state.State == "succeeded" {
				break
			}
			if state.State != "queued" && state.State != "running" {
				ticker.Stop()
				return "", fmt.Errorf("App %s failed: %s", action, state.Error)
			}
			select {
			case <-ctx.Done():
				ticker.Stop()
				return "", ctx.Err()
			case <-ticker.C:
			}
		}
		ticker.Stop()
	}
	for _, in := range m.Snapshot().Instances {
		if in.Digest == digest && in.Scope == scope && (in.State == "ready" || in.State == "prepared") {
			return in.ID, nil
		}
	}
	return "", fmt.Errorf("initial App did not become ready or prepared")
}
