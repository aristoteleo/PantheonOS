// Package apptransport carries a TCP stream over a node-initiated WebSocket.
// NATS carries only the rendezvous; document bytes never enter the message bus.
package apptransport

import (
	"io"
	"net"
	"sync"
	"time"

	"github.com/gorilla/websocket"
)

const ChunkSize = 64 * 1024

type Stream struct {
	ws     *websocket.Conn
	reader io.Reader
	mu     sync.Mutex
}

func New(ws *websocket.Conn) *Stream {
	ws.SetReadLimit(ChunkSize)
	return &Stream{ws: ws}
}

func (s *Stream) Read(p []byte) (int, error) {
	for {
		if s.reader == nil {
			kind, reader, err := s.ws.NextReader()
			if err != nil {
				return 0, err
			}
			if kind != websocket.BinaryMessage {
				return 0, io.ErrUnexpectedEOF
			}
			s.reader = reader
		}
		n, err := s.reader.Read(p)
		if err == io.EOF {
			s.reader = nil
			if n > 0 {
				return n, nil
			}
			continue
		}
		return n, err
	}
}

func (s *Stream) Write(p []byte) (int, error) {
	s.mu.Lock()
	defer s.mu.Unlock()
	written := 0
	for len(p) > 0 {
		n := min(len(p), ChunkSize)
		if err := s.ws.WriteMessage(websocket.BinaryMessage, p[:n]); err != nil {
			return written, err
		}
		written += n
		p = p[n:]
	}
	return written, nil
}
func (s *Stream) Close() error                      { return s.ws.Close() }
func (s *Stream) LocalAddr() net.Addr               { return s.ws.LocalAddr() }
func (s *Stream) RemoteAddr() net.Addr              { return s.ws.RemoteAddr() }
func (s *Stream) SetReadDeadline(t time.Time) error { return s.ws.SetReadDeadline(t) }
func (s *Stream) SetWriteDeadline(t time.Time) error {
	s.mu.Lock()
	defer s.mu.Unlock()
	return s.ws.SetWriteDeadline(t)
}
func (s *Stream) SetDeadline(t time.Time) error {
	if err := s.SetReadDeadline(t); err != nil {
		return err
	}
	return s.SetWriteDeadline(t)
}

// Relay closes both ends when either peer disconnects, including an upgraded
// HTTP/WebSocket connection. It uses bounded buffers and normal TCP backpressure.
func Relay(a, b net.Conn) {
	defer a.Close()
	defer b.Close()
	done := make(chan struct{})
	go func() { _, _ = io.Copy(a, b); _ = a.Close(); _ = b.Close(); close(done) }()
	_, _ = io.Copy(b, a)
	_ = a.Close()
	_ = b.Close()
	<-done
}
