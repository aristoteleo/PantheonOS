package lifecycle

import (
	"context"
	"fmt"
	"time"
)

// CheckInstance authorizes a dependency consumer at admission, not for the
// entire duration of an accepted mutation. A preparation ID permits issuance
// before the next start; callers must omit it when admitting actual RPCs.
// No port, process ID, or caller-supplied endpoint is accepted.
func (m *Manager) CheckInstance(ctx context.Context, id, revision string, generation uint64, preparation string) error {
	ctx, cancel := context.WithTimeout(ctx, 5*time.Second)
	defer cancel()
	invalid := func() error { return fmt.Errorf("App consumer is unavailable at the requested revision/generation") }
	m.mu.Lock()
	in := m.ledger.Instances[id]
	if m.closed || in == nil || in.Digest != revision || generation == 0 {
		m.mu.Unlock()
		return invalid()
	}
	install := m.ledger.Installations[revision]
	if install == nil || install.State != "installed" {
		m.mu.Unlock()
		return invalid()
	}
	if preparation != "" {
		valid := in.State == "prepared" && in.Generation < 1<<63-1 && generation == in.Generation+1 && in.StartPreparationID == preparation && ctx.Err() == nil
		m.mu.Unlock()
		if valid {
			return nil
		}
		return invalid()
	}
	admissible := func(in *Instance) bool {
		if m.closed || in == nil || in.Digest != revision || in.Generation != generation {
			return false
		}
		// Consumers may need a provider to initialize before their own readiness
		// succeeds. A prepared reservation alone cannot call: starting admission
		// requires an actually live owned resource, checked below. ReadyGeneration
		// zero distinguishes the new start from a previously ready generation.
		return (in.State == "starting" && in.ReadyGeneration == 0) ||
			(in.ReadyGeneration == generation && (in.State == "ready" || in.State == "recovered"))
	}
	starting := in.State == "starting"
	if !admissible(in) || len(in.Resources) == 0 || (!starting && len(in.Resources) != len(install.Definition.Components)) {
		m.mu.Unlock()
		return invalid()
	}
	resources := clone(in.Resources)
	m.mu.Unlock()
	// Container inspection can block. Never hold the ledger lock across it.
	live := false
	for _, resource := range resources {
		alive, err := m.driver.Alive(ctx, resource)
		if ctx.Err() != nil || (!starting && (err != nil || !alive)) {
			return invalid()
		}
		if err == nil && alive {
			live = true
		}
	}
	m.mu.Lock()
	defer m.mu.Unlock()
	if !live || !admissible(m.ledger.Instances[id]) || ctx.Err() != nil {
		return invalid()
	}
	return nil
}
