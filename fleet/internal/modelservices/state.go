package modelservices

import (
	"context"
	"fmt"
	"sync"
)

// Tasks are background starts (Modal/deploy engines) the management keeps
// running between calls; lost on restart, re-spawned by the next status call.
type Tasks struct {
	mu      sync.Mutex
	running map[string]bool
}

// Spawn runs fn once per key until it finishes.
func (t *Tasks) Spawn(key string, fn func()) bool {
	t.mu.Lock()
	defer t.mu.Unlock()
	if t.running == nil {
		t.running = map[string]bool{}
	}
	if t.running[key] {
		return false
	}
	t.running[key] = true
	go func() {
		defer func() {
			t.mu.Lock()
			delete(t.running, key)
			t.mu.Unlock()
		}()
		fn()
	}()
	return true
}

// Plans are the durable deploy plans (one JSON file per deployment).
type Plans struct {
	Root string
}

// Groups inspects durable model groups or finishes an explicit stop.
func (m *Manager) Groups(ctx context.Context, action, groupID string) (map[string]any, error) {
	return nil, fmt.Errorf("model groups are not available yet")
}
