package lifecycle

import (
	"fmt"
	"sync"
	"time"
)

const idleGrace = time.Minute
const windowLeaseTTL = 5 * time.Minute

type instanceUsage struct {
	leases         map[string]time.Time
	calls          int
	idleSince      time.Time
	stopping       bool
	reconnectUntil time.Time
}

// All usage is generation-bound and owned by the node, across browsers and
// agents. Leases are ephemeral; keep-alive policy is durable. Old clients do
// not opt in accidentally just by reading status or opening a TCP connection.
func (m *Manager) usageLocked(id string, now time.Time) *instanceUsage {
	u := m.usage[id]
	if u == nil {
		u = &instanceUsage{leases: map[string]time.Time{}, idleSince: now}
		m.usage[id] = u
	}
	for lease, expiry := range u.leases {
		if !expiry.After(now) {
			delete(u.leases, lease)
		}
	}
	if len(u.leases) > 0 || u.calls > 0 {
		u.idleSince = time.Time{}
	} else if u.idleSince.IsZero() {
		u.idleSince = now
	}
	return u
}

func (m *Manager) boundLocked(id, revision string, generation uint64) (*Instance, error) {
	in := m.ledger.Instances[id]
	if m.closed || in == nil || in.Digest != revision || in.Generation != generation {
		return nil, fmt.Errorf("App usage binding is stale; reconnect the window")
	}
	return in, nil
}

func (m *Manager) WindowLease(id, revision string, generation uint64, lease string, release bool) error {
	if !nameRE.MatchString(lease) {
		return fmt.Errorf("invalid window lease")
	}
	m.mu.Lock()
	defer m.mu.Unlock()
	in, err := m.boundLocked(id, revision, generation)
	if err != nil {
		return err
	}
	now := time.Now()
	u := m.usageLocked(id, now)
	if release {
		delete(u.leases, lease)
		m.usageLocked(id, now)
		return nil
	}
	if m.modelEngineFencedLocked(id) {
		return fmt.Errorf("owned model engine is sleeping or changing state; wake its model service first")
	}
	if u.stopping || (in.State != "ready" && in.State != "stop_blocked" && in.State != "recovered") {
		return fmt.Errorf("App is stopping; retry opening after it stops")
	}
	u.leases[lease] = now.Add(windowLeaseTTL)
	u.idleSince = time.Time{}
	if !in.AutoStop {
		in.AutoStop = true
		return m.persist()
	}
	return nil
}

func (m *Manager) SetKeepAlive(id, revision string, generation uint64, keep bool) error {
	m.mu.Lock()
	defer m.mu.Unlock()
	in, err := m.boundLocked(id, revision, generation)
	if err != nil {
		return err
	}
	if in.State == "draining" {
		return fmt.Errorf("App is already stopping")
	}
	if !keep && m.modelIdleOwnsLocked(id) {
		return fmt.Errorf("use the model service idle policy for this owned engine and connector")
	}
	in.KeepAlive, in.AutoStop = keep, true
	u := m.usageLocked(id, time.Now())
	if !u.idleSince.IsZero() {
		u.idleSince = time.Now()
	}
	return m.persist()
}

// Hold every RPC/HTTP tunnel until the response/stream finishes. This covers
// Agent calls and saves even if the last frontend window has already detached.
func (m *Manager) BeginUse(id, revision string, generation uint64) (func(), error) {
	m.mu.Lock()
	defer m.mu.Unlock()
	in, err := m.boundLocked(id, revision, generation)
	if err != nil {
		return nil, err
	}
	if m.modelEngineFencedLocked(id) {
		return nil, fmt.Errorf("owned model engine is sleeping or changing state; wake its model service first")
	}
	u := m.usageLocked(id, time.Now())
	if u.stopping || (in.State != "ready" && in.State != "draining" && in.State != "stop_blocked" && in.State != "recovered") {
		return nil, fmt.Errorf("App is not accepting requests; reconnect after it stops")
	}
	u.calls++
	u.idleSince = time.Time{}
	var once sync.Once
	return func() {
		once.Do(func() {
			m.mu.Lock()
			defer m.mu.Unlock()
			u.calls--
			if len(u.leases) == 0 && u.calls == 0 {
				u.idleSince = time.Now()
			}
		})
	}, nil
}

func (m *Manager) idleDueLocked(in *Instance, now time.Time) bool {
	u := m.usageLocked(in.ID, now)
	return (in.State == "ready" || in.State == "recovered") && in.AutoStop && !in.KeepAlive &&
		len(u.leases) == 0 && u.calls == 0 && !now.Before(u.reconnectUntil) &&
		!u.idleSince.IsZero() && now.Sub(u.idleSince) >= idleGrace
}

func (m *Manager) stopIdle(now time.Time) {
	m.mu.Lock()
	var requests []Request
	for _, in := range m.ledger.Instances {
		if !m.idleDueLocked(in, now) {
			continue
		}
		pending := false
		for _, op := range m.ledger.Operations {
			if op.Request.Digest == in.Digest && op.Request.Scope == in.Scope && (op.State == "queued" || op.State == "running") {
				pending = true
				break
			}
		}
		if !pending {
			requests = append(requests, Request{Protocol: Protocol, OperationID: fmt.Sprintf("idle-%s-%d", in.ID, now.UnixNano()), Action: "stop", Digest: in.Digest, Scope: in.Scope, Generation: in.Generation, IfIdle: true})
		}
	}
	m.mu.Unlock()
	for _, request := range requests {
		_, _ = m.Submit(request)
	}
}
