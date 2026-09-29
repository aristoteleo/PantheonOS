// Package hpcservice supervises allocation-scoped HTTP services over attended SSH.
// A service owns one Slurm step and multiplexes bounded TCP streams over its stdio.
package hpcservice

import (
	"bufio"
	"context"
	_ "embed"
	"encoding/base64"
	"encoding/json"
	"errors"
	"io"
	"net"
	"os"
	"path/filepath"
	"strconv"
	"sync"
	"time"

	"github.com/aristoteleo/pantheon-fleet/internal/hpc"
	"github.com/aristoteleo/pantheon-fleet/internal/hpcproxy"
)

//go:embed supervisor.py
var supervisor string

type Spec = hpc.HTTPService
type Receipt struct {
	Kind       string    `json:"kind,omitempty"`
	Primary    bool      `json:"primary,omitempty"`
	Instance   string    `json:"instance_id"`
	Name       string    `json:"name"`
	Revision   string    `json:"revision"`
	Generation uint64    `json:"generation"`
	State      string    `json:"state"`
	Error      string    `json:"error,omitempty"`
	Hostname   string    `json:"hostname,omitempty"`
	Log        string    `json:"log,omitempty"`
	Updated    time.Time `json:"updated_at"`
}
type frame struct {
	Type     string `json:"type"`
	ID       string `json:"id,omitempty"`
	Data     []byte `json:"data,omitempty"`
	Error    string `json:"error,omitempty"`
	Hostname string `json:"hostname,omitempty"`
}
type channel struct {
	conn net.Conn
	data chan frame
}
type live struct {
	ctx      context.Context
	cancel   context.CancelFunc
	send     chan frame
	done     chan struct{}
	stopping bool
	channels map[string]*channel
	next     uint64
}
type Manager struct {
	mu      sync.Mutex
	root    string
	stream  hpcproxy.Stream
	cluster string
	job     hpc.Job
	records map[string]Receipt
	running map[string]*live
	closed  bool
}

func Open(root string, stream hpcproxy.Stream, cluster string, job hpc.Job) (*Manager, error) {
	if err := os.MkdirAll(root, 0700); err != nil {
		return nil, err
	}
	m := &Manager{root: root, stream: stream, cluster: cluster, job: job, records: map[string]Receipt{}, running: map[string]*live{}}
	data, err := os.ReadFile(filepath.Join(root, "services.json"))
	if err != nil && !os.IsNotExist(err) {
		return nil, err
	}
	if len(data) > 0 {
		if err = json.Unmarshal(data, &m.records); err != nil {
			return nil, err
		}
	}
	for id, rec := range m.records {
		if rec.State == "starting" || rec.State == "running" || rec.State == "stopping" {
			rec.State = "interrupted"
			rec.Error = "Connector restarted; previous step expires after its heartbeat lease"
			rec.Updated = time.Now()
			m.records[id] = rec
		}
	}
	return m, m.persist()
}
func (m *Manager) persist() error {
	data, err := json.Marshal(m.records)
	if err != nil {
		return err
	}
	path := filepath.Join(m.root, "services.json")
	f, err := os.CreateTemp(m.root, ".services-")
	if err != nil {
		return err
	}
	defer os.Remove(f.Name())
	if _, err = f.Write(data); err == nil {
		err = f.Sync()
	}
	closeErr := f.Close()
	if err != nil {
		return err
	}
	if closeErr != nil {
		return closeErr
	}
	return os.Rename(f.Name(), path)
}
func (m *Manager) List() []Receipt {
	m.mu.Lock()
	defer m.mu.Unlock()
	out := make([]Receipt, 0, len(m.records))
	for _, v := range m.records {
		out = append(out, v)
	}
	return out
}
func (m *Manager) Start(ctx context.Context, spec Spec, generation uint64) (Receipt, error) {
	var err error
	spec, err = spec.Normalize()
	if err != nil {
		return Receipt{}, err
	}
	revision := spec.Revision()
	if m.job.Service != nil && (revision != m.job.Service.Revision() || generation != 1) {
		return Receipt{}, errors.New("primary App binding cannot change; submit a new job")
	}

	id := "hpcsvc_" + spec.Name
	m.mu.Lock()
	defer m.mu.Unlock()
	if m.closed || ctx.Err() != nil {
		return Receipt{}, errors.New("allocation unavailable")
	}
	old, exists := m.records[id]
	// Retrying a start after a lost reply cannot create a second process.
	reattach := m.job.Service != nil && exists && old.Generation == generation && old.Revision == revision && m.running[id] == nil && (old.State == "interrupted" || old.State == "failed")
	if exists && old.Generation == generation && old.Revision == revision && !reattach {
		return old, nil
	}
	if generation != old.Generation+1 && !reattach {
		return Receipt{}, errors.New("stale service generation; refresh services")
	}
	if len(m.running) != 0 {
		return Receipt{}, errors.New("one service may run per allocation; stop it first")
	}
	for _, rec := range m.records {
		if m.job.Service == nil && rec.State == "interrupted" && time.Since(rec.Updated) < 40*time.Second {
			return Receipt{}, errors.New("waiting for previous step heartbeat lease to expire")
		}
	}
	if !exists && len(m.records) >= 32 {
		return Receipt{}, errors.New("allocation service history limit reached")
	}
	rec := Receipt{Kind: spec.Kind, Primary: m.job.Service != nil, Instance: id, Name: spec.Name, Revision: revision, Generation: generation, State: "starting", Updated: time.Now()}
	m.records[id] = rec
	if err := m.persist(); err != nil {
		if exists {
			m.records[id] = old
		} else {
			delete(m.records, id)
		}
		return Receipt{}, err
	}
	runctx, cancel := context.WithCancel(ctx)
	l := &live{ctx: runctx, cancel: cancel, send: make(chan frame, 64), done: make(chan struct{}), channels: map[string]*channel{}}
	m.running[id] = l
	go m.run(id, l, spec)
	return rec, nil
}
func enqueue(l *live, f frame) error {
	select {
	case <-l.ctx.Done():
		return errors.New("service disconnected")
	default:
	}
	select {
	case l.send <- f:
		return nil
	case <-l.ctx.Done():
		return errors.New("service disconnected")
	default:
		return errors.New("service transport busy")
	}
}

// Bound buffering without dropping large HTTP bodies or starving slow readers.
func sendData(l *live, f frame) error {
	timer := time.NewTimer(30 * time.Second)
	defer timer.Stop()
	select {
	case l.send <- f:
		return nil
	case <-l.ctx.Done():
		return errors.New("service disconnected")
	case <-timer.C:
		return errors.New("service stream stalled")
	}
}
func (m *Manager) Stop(instance, revision string, generation uint64) (Receipt, error) {
	m.mu.Lock()
	defer m.mu.Unlock()
	rec, ok := m.records[instance]
	if !ok || rec.Revision != revision || rec.Generation != generation {
		return Receipt{}, errors.New("stale service binding")
	}
	if l := m.running[instance]; l != nil {
		l.stopping = true
		rec.State = "stopping"
		rec.Updated = time.Now()
		m.records[instance] = rec
		_ = enqueue(l, frame{Type: "stop"})
		go func() {
			select {
			case <-l.done:
			case <-time.After(6 * time.Second):
				l.cancel()
			}
		}()
	}
	return rec, m.persist()
}
func (m *Manager) Ready(instance, revision string, generation uint64) error {
	m.mu.Lock()
	defer m.mu.Unlock()
	return m.ready(instance, revision, generation)
}
func (m *Manager) ready(instance, revision string, generation uint64) error {
	rec, ok := m.records[instance]
	l := m.running[instance]
	if m.closed || !ok || rec.State != "running" || rec.Revision != revision || rec.Generation != generation || l == nil || l.ctx.Err() != nil {
		return errors.New("service generation is not ready")
	}
	return nil
}
func (m *Manager) Dial(ctx context.Context, instance, revision string, generation uint64) (net.Conn, error) {
	m.mu.Lock()
	if err := m.ready(instance, revision, generation); err != nil {
		m.mu.Unlock()
		return nil, err
	}
	l := m.running[instance]
	if len(l.channels) >= 16 {
		m.mu.Unlock()
		return nil, errors.New("service connection limit reached")
	}
	a, b := net.Pipe()
	l.next++
	key := strconv.FormatUint(l.next, 10)
	ch := &channel{conn: b, data: make(chan frame, 10)}
	l.channels[key] = ch
	err := enqueue(l, frame{Type: "open", ID: key})
	m.mu.Unlock()
	streamDone := make(chan struct{})
	var once sync.Once
	cleanup := func() {
		once.Do(func() {
			close(streamDone)
			m.mu.Lock()
			delete(l.channels, key)
			m.mu.Unlock()
			_ = enqueue(l, frame{Type: "close", ID: key})
			b.Close()
		})
	}
	if err != nil {
		cleanup()
		return nil, err
	}
	select {
	case f := <-ch.data:
		if f.Type != "opened" {
			cleanup()
			return nil, errors.New("service endpoint unavailable")
		}
	case <-ctx.Done():
		cleanup()
		return nil, ctx.Err()
	case <-l.ctx.Done():
		cleanup()
		return nil, errors.New("service disconnected")
	}
	go func() {
		defer cleanup()
		for {
			select {
			case f := <-ch.data:
				if f.Type != "data" {
					return
				}
				b.SetWriteDeadline(time.Now().Add(30 * time.Second))
				if _, err := b.Write(f.Data); err != nil {
					return
				}
				if err := sendData(l, frame{Type: "ack", ID: key}); err != nil {
					return
				}
			case <-l.ctx.Done():
				return
			case <-streamDone:
				return
			}
		}
	}()
	go func() {
		defer cleanup()
		buf := make([]byte, 16384)
		for {
			n, err := b.Read(buf)
			if n > 0 {
				if e := sendData(l, frame{Type: "data", ID: key, Data: append([]byte(nil), buf[:n]...)}); e != nil {
					return
				}
			}
			if err != nil {
				return
			}
		}
	}()
	return a, nil
}
func (m *Manager) run(id string, l *live, spec Spec) {
	input, send := io.Pipe()
	receive, output := io.Pipe()
	defer close(l.done)
	defer l.cancel()
	done := make(chan error, 1)
	payload, _ := json.Marshal(struct {
		Spec
		Allocation string `json:"allocation"`
		JobID      string `json:"job_id"`
		Attach     bool   `json:"attach"`
		Revision   string `json:"revision"`
	}{spec, m.job.AllocationID, m.job.JobID, m.job.Service != nil, spec.Revision()})
	argv := []string{"srun", "--jobid=" + m.job.JobID, "--overlap", "--exact", "--nodes=1", "--ntasks=1", "--cpus-per-task=" + strconv.Itoa(m.job.CPUs), "--mem=" + strconv.Itoa(m.job.MemGB) + "G", "--gres=none", "--unbuffered", "python3", "-u", "-c", supervisor, base64.StdEncoding.EncodeToString(payload)}
	if m.job.Service != nil {
		argv[6] = "--cpus-per-task=1"
		argv[7] = "--mem=256M"
	} else if m.job.GPUs > 0 {
		argv[8] = "--gres=gpu:" + strconv.Itoa(m.job.GPUs)
	}
	go func() {
		err := m.stream(l.ctx, m.cluster, true, input, output, io.Discard, argv...)
		output.CloseWithError(err)
		done <- err
	}()
	go func() {
		defer send.Close()
		ticker := time.NewTicker(5 * time.Second)
		defer ticker.Stop()
		enc := json.NewEncoder(send)
		for {
			var f frame
			select {
			case f = <-l.send:
			case <-ticker.C:
				f = frame{Type: "ping"}
			case <-l.ctx.Done():
				return
			}
			if enc.Encode(f) != nil {
				return
			}
		}
	}()
	go func() { <-l.ctx.Done(); input.Close(); send.Close(); receive.Close(); output.Close() }()
	// Bound srun startup as well as the remote application's own readiness timer.
	startup := time.AfterFunc(time.Duration(spec.StartupSeconds+30)*time.Second, func() {
		m.mu.Lock()
		starting := m.records[id].State == "starting"
		m.mu.Unlock()
		if starting {
			l.cancel()
		}
	})
	defer startup.Stop()
	scanner := bufio.NewScanner(receive)
	scanner.Buffer(make([]byte, 4096), 100000)
	for scanner.Scan() {
		var f frame
		if json.Unmarshal(scanner.Bytes(), &f) != nil {
			break
		}
		m.mu.Lock()
		rec := m.records[id]
		switch f.Type {
		case "ready":
			if !l.stopping {
				rec.State = "running"
				rec.Hostname = f.Hostname
				rec.Updated = time.Now()
				m.records[id] = rec
				_ = m.persist()
			}
		case "error":
			rec.Error = f.Error
			m.records[id] = rec
		case "log":
			rec.Log += string(f.Data)
			if len(rec.Log) > 8192 {
				rec.Log = rec.Log[len(rec.Log)-8192:]
			}
			m.records[id] = rec
		case "opened", "data", "close":
			if ch := l.channels[f.ID]; ch != nil {
				select {
				case ch.data <- f:
				default:
					ch.conn.Close()
					delete(l.channels, f.ID)
					_ = enqueue(l, frame{Type: "close", ID: f.ID})
				}
			}
		}
		m.mu.Unlock()
	}
	// Closing the input asks the remote supervisor to reap its entire process group.
	send.Close()
	input.Close()
	l.cancel()
	err := <-done
	m.mu.Lock()
	defer m.mu.Unlock()
	rec := m.records[id]
	rec.Updated = time.Now()
	if l.stopping && (err == nil || m.job.Service != nil) {
		rec.State = "stopped"
	} else if err == nil && rec.Error != "" {
		rec.State = "failed"
	} else {
		rec.State = "interrupted"
		if rec.Error == "" {
			rec.Error = "Service transport ended; step expires with the connector heartbeat lease"
		}
	}
	for _, ch := range l.channels {
		ch.conn.Close()
	}
	l.channels = map[string]*channel{}
	m.records[id] = rec
	delete(m.running, id)
	_ = m.persist()
}
func (m *Manager) Close() {
	m.mu.Lock()
	m.closed = true
	var pending []<-chan struct{}
	for _, l := range m.running {
		l.cancel()
		pending = append(pending, l.done)
	}
	m.mu.Unlock()
	deadline := time.NewTimer(8 * time.Second)
	defer deadline.Stop()
	for _, done := range pending {
		select {
		case <-done:
		case <-deadline.C:
			return
		}
	}
}
func (m *Manager) Busy() bool { m.mu.Lock(); defer m.mu.Unlock(); return len(m.running) > 0 }
