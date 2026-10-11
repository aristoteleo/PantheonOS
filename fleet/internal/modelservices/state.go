package modelservices

import (
	"context"
	"fmt"
	"sync"
)

// Tasks are background engine starts (Modal/deploy) kept between calls; lost
// on restart and re-spawned by the next status call.
type Tasks struct {
	mu      sync.Mutex
	running map[string]bool
	failed  map[string]string
}

// Start reports a finished task's failure once (and forgets it), or runs fn
// unless it already runs. running is true while a task is in flight.
func (t *Tasks) Start(key string, fn func() error) (failure string, running bool) {
	t.mu.Lock()
	defer t.mu.Unlock()
	if t.running == nil {
		t.running, t.failed = map[string]bool{}, map[string]string{}
	}
	if t.running[key] {
		return "", true
	}
	if f, ok := t.failed[key]; ok {
		delete(t.failed, key)
		return f, false
	}
	t.running[key] = true
	go func() {
		err := fn()
		t.mu.Lock()
		defer t.mu.Unlock()
		delete(t.running, key)
		if err != nil {
			t.failed[key] = err.Error()
		}
	}()
	return "", true
}

// Plans are the durable deploy plans (one JSON file per deployment).
type Plans struct {
	Root string
}

// Groups inspects durable model groups or finishes an explicit stop.
func (m *Manager) Groups(ctx context.Context, action, groupID string) (map[string]any, error) {
	return nil, fmt.Errorf("model groups are not available yet")
}
