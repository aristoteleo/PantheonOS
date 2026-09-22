package appdirect

import (
	"bytes"
	"context"
	"encoding/binary"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net"
	"sync"
	"time"

	"github.com/aristoteleo/pantheon-fleet/internal/dataplane"
)

// Version 2 keeps a peer alive between grants, not an App authorization. Each G
// opens a fresh, single-use authorized QUIC stream. There is one stream at a
// time; C is a barrier after all old stream writes have finished. The private
// pipe has a byte-credit window in both directions so an unread response cannot
// grow memory indefinitely or prevent the parent from sending X (cancel/close).
const sessionFrameLimit = 64 * 1024
const sessionWindow = 256 * 1024

func readFrame(r io.Reader) (byte, []byte, error) {
	var header [5]byte
	if _, err := io.ReadFull(r, header[:]); err != nil {
		return 0, nil, err
	}
	n := binary.BigEndian.Uint32(header[1:])
	if n > sessionFrameLimit {
		return 0, nil, fmt.Errorf("direct session frame too large")
	}
	body := make([]byte, int(n))
	_, err := io.ReadFull(r, body)
	return header[0], body, err
}

func writeFrame(w io.Writer, kind byte, body []byte) error {
	var header [5]byte
	header[0] = kind
	binary.BigEndian.PutUint32(header[1:], uint32(len(body)))
	for _, data := range [][]byte{header[:], body} {
		for len(data) > 0 {
			n, err := w.Write(data)
			if err != nil {
				return err
			}
			if n == 0 {
				return io.ErrShortWrite
			}
			data = data[n:]
		}
	}
	return nil
}

type sessionJob struct {
	ctx          context.Context
	cancel       context.CancelFunc
	mu           sync.Mutex
	pending      []byte
	inputCredit  int
	outputCredit int
	inputWake    chan struct{}
	outputWake   chan struct{}
}

func notifySession(wake chan struct{}) {
	select {
	case wake <- struct{}{}:
	default:
	}
}

func (j *sessionJob) accept(kind byte, body []byte) error {
	j.mu.Lock()
	defer j.mu.Unlock()
	switch kind {
	case 'D':
		if len(body) == 0 || len(body) > j.inputCredit {
			return fmt.Errorf("direct session input credit exceeded")
		}
		j.inputCredit -= len(body)
		j.pending = append(j.pending, body...)
		notifySession(j.inputWake)
	case 'W':
		if len(body) != 4 {
			return fmt.Errorf("invalid direct session credit")
		}
		n := int(binary.BigEndian.Uint32(body))
		if n <= 0 || n > sessionWindow-j.outputCredit {
			return fmt.Errorf("direct session output credit exceeded")
		}
		j.outputCredit += n
		notifySession(j.outputWake)
	default:
		return fmt.Errorf("unexpected direct session frame")
	}
	return nil
}

func (j *sessionJob) upload(ctx context.Context, conn net.Conn, send func(byte, []byte) error) {
	for {
		j.mu.Lock()
		n := min(len(j.pending), sessionFrameLimit)
		data := append([]byte(nil), j.pending[:n]...)
		j.pending = j.pending[n:]
		j.mu.Unlock()
		if n == 0 {
			select {
			case <-ctx.Done():
				return
			case <-j.inputWake:
				continue
			}
		}
		if _, err := io.Copy(conn, bytes.NewReader(data)); err != nil {
			return
		}
		j.mu.Lock()
		j.inputCredit += n
		j.mu.Unlock()
		var credit [4]byte
		binary.BigEndian.PutUint32(credit[:], uint32(n))
		if send('W', credit[:]) != nil {
			return
		}
	}
}

func (j *sessionJob) download(ctx context.Context, conn net.Conn, send func(byte, []byte) error) {
	buffer := make([]byte, sessionFrameLimit)
	for {
		j.mu.Lock()
		allowed := min(j.outputCredit, len(buffer))
		j.mu.Unlock()
		if allowed == 0 {
			select {
			case <-ctx.Done():
				return
			case <-j.outputWake:
				continue
			}
		}
		n, err := conn.Read(buffer[:allowed])
		if n > 0 {
			j.mu.Lock()
			j.outputCredit -= n
			j.mu.Unlock()
			if send('D', buffer[:n]) != nil {
				return
			}
		}
		if err != nil {
			return
		}
	}
}

// RunSession serves a bounded private stdio session. No TCP HTTP proxy or new
// remotely exposed listener is introduced. Parent EOF, termination and an idle
// timeout close the peer. Active grants retain Dial's expiry/generation checks.
func RunSession(ctx context.Context, input io.ReadCloser, output io.WriteCloser) error {
	return runSession(ctx, input, output, 30*time.Second)
}

func runSession(ctx context.Context, input io.ReadCloser, output io.WriteCloser, idle time.Duration) error {
	ctx, cancel := context.WithCancel(ctx)
	defer cancel()
	defer input.Close()
	defer output.Close()
	closed := make(chan struct{})
	go func() {
		defer close(closed)
		<-ctx.Done()
		_ = input.Close()
		_ = output.Close()
	}()
	defer func() { cancel(); <-closed }()
	p, err := dataplane.NewAppClient(ctx)
	if err != nil {
		return fmt.Errorf("direct workload peer unavailable")
	}
	defer p.Close()
	if err := json.NewEncoder(output).Encode(map[string]any{"protocol": 2, "peer_id": p.ID()}); err != nil {
		return err
	}
	var stateMu, writeMu sync.Mutex
	var current *sessionJob
	var jobs sync.WaitGroup
	defer func() { cancel(); jobs.Wait() }()
	lastIdle := time.Now()
	ticker := time.NewTicker(min(idle, time.Second))
	defer ticker.Stop()
	go func() {
		for {
			select {
			case <-ctx.Done():
				return
			case now := <-ticker.C:
				stateMu.Lock()
				if current == nil && now.Sub(lastIdle) >= idle {
					cancel()
				}
				stateMu.Unlock()
			}
		}
	}()
	send := func(kind byte, body []byte) error {
		writeMu.Lock()
		defer writeMu.Unlock()
		err := writeFrame(output, kind, body)
		if err != nil {
			cancel()
		}
		return err
	}
	for {
		kind, body, err := readFrame(input)
		if err != nil {
			return err
		}
		stateMu.Lock()
		job := current
		if kind == 'G' && job == nil && ctx.Err() == nil {
			jobCtx, stop := context.WithCancel(ctx)
			job = &sessionJob{ctx: jobCtx, cancel: stop, inputCredit: sessionWindow, outputCredit: sessionWindow,
				inputWake: make(chan struct{}, 1), outputWake: make(chan struct{}, 1)}
			current = job
			jobs.Add(1)
			go func(job *sessionJob, body []byte) {
				defer jobs.Done()
				defer job.cancel()
				defer func() {
					// Serialize C before any new job's output, but never hold
					// the state lock across a possibly blocked pipe write. The
					// idle watchdog and parent EOF must still be able to stop us.
					writeMu.Lock()
					stateMu.Lock()
					current = nil
					lastIdle = time.Now()
					stateMu.Unlock()
					if writeFrame(output, 'C', nil) != nil {
						cancel()
					}
					writeMu.Unlock()
				}()
				job.run(p, body, send)
			}(job, body)
			stateMu.Unlock()
			continue
		}
		stateMu.Unlock()
		if job == nil || kind == 'G' {
			return fmt.Errorf("direct session stream boundary required")
		}
		if kind == 'X' && len(body) == 0 {
			job.cancel()
		} else if err := job.accept(kind, body); err != nil {
			return err
		}
	}
}

func (j *sessionJob) run(p *dataplane.Plane, body []byte, send func(byte, []byte) error) {
	var grant Grant
	decoder := json.NewDecoder(bytes.NewReader(body))
	decoder.DisallowUnknownFields()
	if decoder.Decode(&grant) != nil || decoder.Decode(new(any)) != io.EOF {
		_ = send('F', []byte("grant_rejected"))
		<-j.ctx.Done()
		return
	}
	// Dial's connection context must remain alive for the whole stream. A timer
	// bounds setup separately; it is stopped as soon as authentication finishes.
	ctx, stop := context.WithCancel(j.ctx)
	defer stop()
	timer := time.AfterFunc(18*time.Second, stop)
	conn, err := Dial(ctx, p, grant)
	timer.Stop()
	if err != nil {
		code := "unavailable"
		if errors.Is(err, ErrInvalidGrant) || errors.Is(err, ErrGrantRejected) {
			code = "grant_rejected"
		}
		_ = send('F', []byte(code))
		<-j.ctx.Done()
		return
	}
	defer conn.Close()
	if send('R', nil) != nil {
		return
	}
	ended := make(chan struct{}, 2)
	go func() { j.upload(ctx, conn, send); ended <- struct{}{} }()
	go func() { j.download(ctx, conn, send); ended <- struct{}{} }()
	select {
	case <-j.ctx.Done():
	case <-ended:
		ended <- struct{}{}
	}
	stop()
	_ = conn.Close()
	<-ended
	<-ended
	_ = send('E', nil)
	// Only the parent's explicit X releases the stream. This keeps late read
	// credits in the old stream until C, even when HTTP EOF arrived first.
	<-j.ctx.Done()
}
