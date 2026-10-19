package appsvc

// Session leases describe provider-owned ephemeral resources, not permissions.
// The owner coordinator calls this surface through authenticated App RPC and
// binds the resulting session ID into a separate consumer grant. Neither an
// owner_ref nor a session ID is a credential. No Agent concepts belong here.

import (
	"context"
	"fmt"
	"math"
	"regexp"
	"sync"
	"time"
)

var sessionName = regexp.MustCompile(`^[A-Za-z0-9_-]{1,100}$`)
var sessionLeaseID = regexp.MustCompile(`^[a-f0-9]{64}$`)

type SessionReceipt struct {
	LeaseID   string `json:"lease_id"`
	OwnerRef  string `json:"owner_ref"`
	Kind      string `json:"kind"`
	SessionID string `json:"session_id"`
	State     string `json:"state"`
	Expires   int64  `json:"expires"`
}

// SessionResource callbacks operate on a local provider resource. They must be
// bounded and must not call back into the registry. For durable/borrowed objects,
// Close must release only this lease's attachment, not destroy the user's data.
// A provider registers only kinds whose expiry policy it can safely implement.
type SessionResource struct {
	ID    string
	Alive func() bool
	Close func() error
}

type sessionLease struct {
	mu sync.Mutex
	SessionReceipt
	resource SessionResource
	terminal string
}

// SessionRegistry keeps tombstones for the provider process lifetime. An acquire
// retried after a lost reply cannot create a second resource or resurrect an
// expired one. Provider generation pinning must reject handles after restart.
// Capacity includes tombstones: exhaustion fails closed rather than forgetting
// a request and replaying creation. Production callers use one stable random
// lease ID per acquisition intent, never one per retry or per Run.
type SessionRegistry struct {
	mu      sync.Mutex
	entries map[string]*sessionLease
	create  func(string) (SessionResource, error)
	now     func() time.Time
	limit   int
	closed  bool
	stop    chan struct{}
	done    chan struct{}
	once    sync.Once
}

func NewSessionRegistry(create func(string) (SessionResource, error)) *SessionRegistry {
	s := &SessionRegistry{entries: map[string]*sessionLease{}, create: create, now: time.Now,
		limit: 4096, stop: make(chan struct{}), done: make(chan struct{})}
	go func() {
		defer close(s.done)
		ticker := time.NewTicker(time.Second)
		defer ticker.Stop()
		for {
			select {
			case <-ticker.C:
				s.Sweep()
			case <-s.stop:
				return
			}
		}
	}()
	return s
}

func sessionRequest(owner, lease string, ttl int) error {
	if !sessionName.MatchString(owner) || !sessionLeaseID.MatchString(lease) || ttl < 30 || ttl > 900 {
		return fmt.Errorf("invalid resource session owner, lease or lifetime")
	}
	return nil
}

func (s *SessionRegistry) Acquire(owner, lease, kind string, ttl int) (SessionReceipt, error) {
	if err := sessionRequest(owner, lease, ttl); err != nil || !sessionName.MatchString(kind) {
		return SessionReceipt{}, fmt.Errorf("invalid resource session acquisition")
	}
	s.mu.Lock()
	if s.closed {
		s.mu.Unlock()
		return SessionReceipt{}, fmt.Errorf("resource session provider is stopping")
	}
	if old := s.entries[lease]; old != nil {
		s.mu.Unlock()
		old.mu.Lock()
		defer old.mu.Unlock()
		if old.OwnerRef != owner || old.Kind != kind {
			return SessionReceipt{}, fmt.Errorf("resource session acquisition intent cannot change")
		}
		s.observeLocked(old)
		return old.SessionReceipt, nil // Acquire is not renewal.
	}
	if len(s.entries) >= s.limit {
		s.mu.Unlock()
		return SessionReceipt{}, fmt.Errorf("resource session receipt capacity reached")
	}
	entry := &sessionLease{SessionReceipt: SessionReceipt{OwnerRef: owner, LeaseID: lease, Kind: kind,
		State: "failed", Expires: s.now().Add(time.Duration(ttl) * time.Second).Unix()}}
	entry.mu.Lock()
	defer entry.mu.Unlock()
	s.entries[lease] = entry // Reserve intent even if creation fails.
	s.mu.Unlock()
	resource, err := s.create(kind)
	if err != nil || resource.ID == "" || resource.Alive == nil || resource.Close == nil {
		if resource.Close != nil {
			entry.resource, entry.SessionID = resource, resource.ID
			entry.State, entry.terminal = "closing", "failed"
			s.observeLocked(entry)
		}
		return entry.SessionReceipt, nil
	}
	entry.resource, entry.SessionID, entry.State = resource, resource.ID, "active"
	s.observeLocked(entry)
	return entry.SessionReceipt, nil
}

func (s *SessionRegistry) find(owner, lease string) (*sessionLease, error) {
	if err := sessionRequest(owner, lease, 30); err != nil {
		return nil, err
	}
	s.mu.Lock()
	entry := s.entries[lease]
	s.mu.Unlock()
	if entry == nil || entry.OwnerRef != owner {
		return nil, fmt.Errorf("resource session lease not found")
	}
	return entry, nil
}

func (s *SessionRegistry) Get(owner, lease string) (SessionReceipt, error) {
	entry, err := s.find(owner, lease)
	if err != nil {
		return SessionReceipt{}, err
	}
	entry.mu.Lock()
	defer entry.mu.Unlock()
	s.observeLocked(entry)
	return entry.SessionReceipt, nil
}

func (s *SessionRegistry) Renew(owner, lease string, ttl int) (SessionReceipt, error) {
	if err := sessionRequest(owner, lease, ttl); err != nil {
		return SessionReceipt{}, err
	}
	entry, err := s.find(owner, lease)
	if err != nil {
		return SessionReceipt{}, err
	}
	entry.mu.Lock()
	defer entry.mu.Unlock()
	s.observeLocked(entry)
	s.mu.Lock()
	if !s.closed && entry.State == "active" {
		entry.Expires = max(entry.Expires, s.now().Add(time.Duration(ttl)*time.Second).Unix())
	}
	s.mu.Unlock()
	return entry.SessionReceipt, nil
}

func (s *SessionRegistry) Release(owner, lease string) (SessionReceipt, error) {
	entry, err := s.find(owner, lease)
	if err != nil {
		return SessionReceipt{}, err
	}
	entry.mu.Lock()
	defer entry.mu.Unlock()
	if entry.State == "active" {
		entry.State, entry.terminal = "closing", "released"
	}
	s.observeLocked(entry)
	return entry.SessionReceipt, nil
}

func (s *SessionRegistry) observeLocked(entry *sessionLease) {
	s.mu.Lock()
	now := s.now()
	s.mu.Unlock()
	if entry.State == "active" {
		if !entry.resource.Alive() {
			entry.State, entry.terminal = "closing", "lost"
		} else if now.Unix() >= entry.Expires {
			entry.State, entry.terminal = "closing", "expired"
		}
	}
	if entry.State == "closing" && entry.resource.Close() == nil {
		entry.State = entry.terminal
		entry.resource = SessionResource{} // Keep only the public tombstone.
	}
}

func (s *SessionRegistry) Sweep() {
	for _, entry := range s.snapshot() {
		if !entry.mu.TryLock() {
			continue // An acquisition/release owns it; do not stall other leases.
		}
		s.observeLocked(entry)
		entry.mu.Unlock()
	}
}

func (s *SessionRegistry) snapshot() []*sessionLease {
	s.mu.Lock()
	defer s.mu.Unlock()
	entries := make([]*sessionLease, 0, len(s.entries))
	for _, entry := range s.entries {
		entries = append(entries, entry)
	}
	return entries
}

func (s *SessionRegistry) Close() error {
	s.mu.Lock()
	s.closed = true
	s.mu.Unlock()
	s.once.Do(func() { close(s.stop) })
	<-s.done
	var incomplete bool
	for _, entry := range s.snapshot() {
		entry.mu.Lock()
		if entry.State == "active" {
			entry.State, entry.terminal = "closing", "released"
		}
		s.observeLocked(entry)
		incomplete = incomplete || entry.State == "closing"
		entry.mu.Unlock()
	}
	if incomplete {
		return fmt.Errorf("resource sessions still require cleanup")
	}
	return nil
}

// SessionHandlers implements resource-session@1. register observes receipts so
// the provider can fence its normal tools when a managed session is no longer
// active. It must not call back into this registry. The manifest must declare
// all four tools and signatures; they are not implicitly exposed by appsvc.
func SessionHandlers(s *SessionRegistry, register func(SessionReceipt)) map[string]Handler {
	handlers := map[string]Handler{}
	for _, name := range []string{"resource_session_acquire", "resource_session_get", "resource_session_renew", "resource_session_release"} {
		handlers[name] = func(_ context.Context, args map[string]any) (any, error) {
			owner, _ := args["owner_ref"].(string)
			lease, _ := args["lease_id"].(string)
			ttl := 900
			if name == "resource_session_acquire" || name == "resource_session_renew" {
				if value, ok := args["ttl_seconds"]; ok {
					switch v := value.(type) {
					case int:
						ttl = v
					case float64:
						if math.IsNaN(v) || math.IsInf(v, 0) || v != math.Trunc(v) || v < 30 || v > 900 {
							return nil, fmt.Errorf("invalid resource session lifetime")
						}
						ttl = int(v)
					default:
						return nil, fmt.Errorf("invalid resource session lifetime")
					}
				}
			}
			var receipt SessionReceipt
			var err error
			switch name {
			case "resource_session_acquire":
				kind, _ := args["kind"].(string)
				receipt, err = s.Acquire(owner, lease, kind, ttl)
			case "resource_session_get":
				receipt, err = s.Get(owner, lease)
			case "resource_session_renew":
				receipt, err = s.Renew(owner, lease, ttl)
			case "resource_session_release":
				receipt, err = s.Release(owner, lease)
			}
			if err == nil && register != nil {
				register(receipt)
			}
			return receipt, err
		}
	}
	return handlers
}
